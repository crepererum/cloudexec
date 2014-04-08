import aiozmq
import asyncio
import base64
import cloudexec.common
import contextlib
from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver
import logging
import os
import paramiko
import uuid


class Vm(object):
    def __init__(self, driver, image_id, size_id, key):
        self.driver = driver
        self.key = key
        self.destroyed = False

        logging.info('Get VM')
        image_filtered = [
            i
            for i in self.driver.list_images()
            if i.id == image_id
        ]
        size_filtered = [
            s
            for s in self.driver.list_sizes()
            if s.id == size_id
        ]

        if not image_filtered:
            logging.error('No VM image found')
            exit(1)
        if not size_filtered:
            logging.error('No VM size found')
            exit(1)

        name = 'cloudexec_' + str(uuid.uuid4())
        image = image_filtered[0]
        size = size_filtered[0]

        self.kpair = driver.import_key_pair_from_file(
            key_file_path=self.key.name_pub,
            name=name
        )

        self.node = self.driver.create_node(
            name=name,
            size=size,
            image=image,
            ex_keyname=name
        )
        self.ip = self.driver.wait_until_running([self.node])[0][1][0]
        logging.info('VM is up and running')

        self.setup()

    def __del__(self):
        self.destroy()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.destroy()

    def destroy(self):
        if not self.destroyed:
            logging.info('Destroy VM')
            self.driver.destroy_node(self.node)
            self.driver.delete_key_pair(self.kpair)
            self.destroyed = True
            logging.info('VM was successfully deleted')

    def setup(self):
        logging.info('Start VM setup')
        with contextlib.ExitStack() as stack:
            # establish connection
            client = paramiko.client.SSHClient()
            client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
            client.connect(
                self.ip,
                username='root',
                key_filename=self.key.name,
                look_for_keys=False
            )
            stack.callback(client.close)

            # secure account by deleting password
            cloudexec.common.wrap_execute(
                client,
                'passwd -d root',
                pipe_out=cloudexec.common.NULLPIPE,
                pipe_err=cloudexec.common.NULLPIPE
            )

            # update and install required packages
            method_apt = \
                'apt-get -y update' \
                ' && apt-get -y upgrade' \
                ' && apt-get -y install sshfs'
            method_yum = \
                'yum -y update' \
                ' && yup -y install fuse-sshfs'
            method_pacmam = \
                'pacman -Suy --noconfirm' \
                ' && pacman -S sshfs --noconfirm'
            method_all = ' || '.join(
                '({})'.format(s)
                for s in (method_apt, method_yum, method_pacmam)
            )
            cloudexec.common.wrap_execute(
                client,
                method_all,
                pipe_out=cloudexec.common.NULLPIPE,
                pipe_err=cloudexec.common.NULLPIPE
            )
        logging.info('Finished VM setup')


class ServerHandler(aiozmq.rpc.AttrHandler):
    def __init__(self, config, tmpdir):
        self.config = config
        self.tmpdir = tmpdir
        self.drivers = {}
        self.vms = {}

    def __del__(self):
        for vm in self.vms.values():
            vm.destroy()

    @aiozmq.rpc.method
    def get_container(self, profile: str):
        if profile not in self.vms:
            self.create_vm(profile)
        vm = self.vms[profile]
        return cloudexec.common.Container(vm.ip, 'root', vm.key.name)

    def create_driver(self, account):
        if account not in self.config['accounts']:
            raise cloudexec.common.RequestException(
                'Unknown account "{}"'.format(account)
            )

        aconfig = self.config['accounts'][account]
        try:
            logging.info('Connect to provider')

            pname = str(aconfig['provider']).upper()
            if not hasattr(Provider, pname):
                raise cloudexec.common.RequestException(
                    'Unknown provider "{}"'.format(pname)
                )
            cls = get_driver(
                getattr(Provider, pname)
            )

            self.drivers[account] = cls(
                str(aconfig['username']),
                str(aconfig['api_key']),
                region=str(aconfig['region'])
            )

            logging.info('Provider details are fine')
        except KeyError as e:
            raise cloudexec.common.RequestException(
                'Invalid account configuration for account "{0}"'
                ', "{1}" attribute is missing'
                .format(account, e.args[0])
            )

    def create_vm(self, profile):
        if profile not in self.config['profiles']:
            raise cloudexec.common.RequestException(
                'Unknown profile "{}"'.format(profile)
            )

        pconfig = self.config['profiles'][profile]
        try:
            account = pconfig['account']
            if account not in self.drivers:
                self.create_driver(account)
            driver = self.drivers[account]

            key_id = str(
                base64.encodestring(bytes(profile, 'utf-8')),
                'utf-8'
            )[:-2]
            key_ssh = cloudexec.common.Key(
                name=self.tmpdir.name
                + '/key.ssh.'
                + key_id
            )
            print("foo")

            self.vms[profile] = Vm(
                driver=driver,
                image_id=str(pconfig['image_id']),
                size_id=str(pconfig['size_id']),
                key=key_ssh
            )
        except KeyError as e:
            raise cloudexec.common.RequestException(
                'Invalid profile configuration for profile "{0}"'
                ', "{1}" attribute is missing'
                .format(profile, e.args[0])
            )


@asyncio.coroutine
def coro_daemon(path, config, tmpdir):
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        os.chmod(path, 0o700)

    yield from aiozmq.rpc.serve_rpc(
        ServerHandler(config, tmpdir),
        bind='ipc://{}/socket'.format(path),
        translation_table=cloudexec.common.RPC_TRANSLATION_TABLE
    )

import aiozmq
import asyncio
import base64
import cloudexec.common
import contextlib
from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver
import logging
import paramiko
import uuid
import yaml


class Vm(object):
    def __init__(self, driver, image_id, size_id, key):
        self.driver = driver
        self.key = key
        self.destroyed = False
        self.node = None
        self.kpair = None

        logging.info('Get VM')
        images = self.driver.list_images()
        sizes = self.driver.list_sizes()

        image_filtered = [
            i
            for i in images
            if i.id == image_id
        ]
        size_filtered = [
            s
            for s in sizes
            if s.id == size_id
        ]

        if not image_filtered:
            raise cloudexec.common.RequestException(
                'No VM image with ID "{0}" found\n'
                'Try one of these:\n'
                '{1}'
                .format(image_id, yaml.dump([
                    {'name': img.name, 'id': img.id}
                    for img in images
                ], default_flow_style=False))
            )
        if not size_filtered:
            raise cloudexec.common.RequestException(
                'No VM size with ID "{0}" found\n'
                'Try one of these:\n'
                '{1}'
                .format(size_id, yaml.dump([
                    {'name': size.name, 'id': size.id}
                    for size in sizes
                ], default_flow_style=False))
            )

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
        self.ip_address = self.driver.wait_until_running([self.node])[0][1][0]
        logging.info('VM is up and running')

        self.setup()

    def __del__(self):
        self.destroy()

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.destroy()

    def destroy(self):
        if not self.destroyed:
            logging.info('Destroy VM')
            if self.node:
                self.driver.destroy_node(self.node)
            if self.kpair:
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
                self.ip_address,
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
        for machine in self.vms.values():
            machine.destroy()

    @aiozmq.rpc.method
    def get_container(self, profile: str):
        if profile not in self.vms:
            self.create_vm(profile)
        machine = self.vms[profile]
        return cloudexec.common.Container(
            machine.ip_address,
            'root',
            machine.key.name
        )

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
        except KeyError as exc:
            raise cloudexec.common.RequestException(
                'Invalid account configuration for account "{0}"'
                ', "{1}" attribute is missing'
                .format(account, exc.args[0])
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
            )[:-1].replace('=', '').replace('+', '-').replace('/', '_')
            key_ssh = cloudexec.common.Key(
                name=self.tmpdir.name
                + '/key.ssh.'
                + key_id
            )

            self.vms[profile] = Vm(
                driver=driver,
                image_id=str(pconfig['image_id']),
                size_id=str(pconfig['size_id']),
                key=key_ssh
            )
        except KeyError as exc:
            raise cloudexec.common.RequestException(
                'Invalid profile configuration for profile "{0}"'
                ', "{1}" attribute is missing'
                .format(profile, exc.args[0])
            )


@asyncio.coroutine
def coro_daemon(path, config, tmpdir):
    yield from aiozmq.rpc.serve_rpc(
        ServerHandler(config, tmpdir),
        bind='ipc://{}/socket'.format(path),
        translation_table=cloudexec.common.RPC_TRANSLATION_TABLE
    )

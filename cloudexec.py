#!/usr/bin/env python3

import argparse
import contextlib
from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver
import os
import os.path
import paramiko.client
import pwd
import shlex
import socket
import subprocess
import sys
import tempfile
import uuid
import yaml


NULLPIPE = open(os.devnull, 'rb+')


class Key(object):
    def __init__(self, name):
        self.name = name
        self.name_pub = self.name + '.pub'

        print('Generate key...', end='')
        sys.stdout.flush()
        subprocess.check_call(
            ['ssh-keygen', '-t', 'rsa', '-f', self.name, '-P', ''],
            stdout=subprocess.DEVNULL
        )
        print('OK')


class Vm(object):
    def __init__(self, driver, image_id, size_id, key):
        self.driver = driver
        self.key = key

        print('Get VM...', end='')
        sys.stdout.flush()
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
            print('Error: No image found!')
            exit(1)
        if not size_filtered:
            print('Error: No size found!')
            exit(1)

        name = 'cloudexec_' + str(uuid.uuid4())
        image = image_filtered[0]
        size = size_filtered[0]

        self.node = self.driver.create_node(
            name=name,
            size=size,
            image=image
        )
        self.ip = self.driver.wait_until_running([self.node])[0][1][0]
        print('OK')

        self.setup(str(self.node.extra['password']))

    def __del__(self):
        print('Destroy VM...', end='')
        self.driver.destroy_node(self.node)
        print('OK')

    def setup(self, password):
        print('Setup VM...', end='')
        sys.stdout.flush()
        with contextlib.ExitStack() as stack:
            # establish connection
            client = paramiko.client.SSHClient()
            client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
            client.connect(
                self.ip,
                username='root',
                password=password
            )
            stack.callback(client.close)

            # secure account by enable key login and delete password
            sftp = client.open_sftp()
            stack.callback(sftp.close)
            if '.ssh' not in sftp.listdir():
                sftp.mkdir('.ssh')
            sftp.put(self.key.name_pub, '.ssh/authorized_keys')
            wrap_execute(client, 'passwd -d')

            # update and install required packages
            wrap_execute(
                client,
                'pacman -Suy --noconfirm && pacman -S sshfs --noconfirm',
                pipe_out=NULLPIPE,
                pipe_err=NULLPIPE
            )
        print('OK')


class Sshd(object):
    def __init__(self, key_host, key_auth, workdir):
        self.key_auth = key_auth
        cfgpath = workdir.name + '/sshd.conf'
        logpath = workdir.name + '/sshd.log'
        pidfile = workdir.name + '/sshd.pid'

        print('Start sshd...', end='')
        sys.stdout.flush()

        self.port = self.find_port(8000)

        with open(cfgpath, 'w') as cfgfile:
            cfgfile.write(
                'Port {3}\n'
                'HostKey {0}\n'
                'LogLevel DEBUG\n'
                'AuthorizedKeysFile {1}\n'
                'UsePrivilegeSeparation no\n'
                'PidFile {2}\n'
                'Subsystem sftp /usr/lib/ssh/sftp-server\n'
                'UsePAM no\n'
                'PasswordAuthentication no\n'
                'ChallengeResponseAuthentication no\n'
                'StrictModes no\n'
                .format(
                    key_host.name,
                    self.key_auth.name_pub,
                    pidfile,
                    self.port
                )
            )

        self.process = subprocess.Popen(
            ['/usr/bin/sshd', '-f', cfgpath, '-E', logpath],
            stdout=subprocess.DEVNULL
        )
        print('OK')

    def __del__(self):
        print('Stop sshd...', end='')
        self.process.terminate()
        print('OK')

    def find_port(self, start):
        port = start - 1
        ok = False
        while not ok:
            port += 1
            with socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            ) as test_socket:
                ok = test_socket.connect_ex(('127.0.0.1', port)) != 0
        return port


def wrap_execute(
        client,
        command,
        pipe_in=sys.stdin.buffer,
        pipe_out=sys.stdout.buffer,
        pipe_err=sys.stderr.buffer
        ):
    with contextlib.ExitStack() as stack:
        channel = client.get_transport().open_session()
        stack.callback(channel.close)

        channel.exec_command(command)
        terminate = False
        while not terminate:
            out = b''
            err = b''

            if channel.recv_ready():
                out = channel.recv(1)
            if channel.recv_stderr_ready():
                err = channel.recv_stderr(1)

            if out:
                pipe_out.write(out)
                pipe_out.flush()
            if err:
                pipe_err.write(err)
                pipe_err.flush()

            if channel.exit_status_ready() and not out and not err:
                terminate = True
        return channel.recv_exit_status()


def get_user():
    return pwd.getpwuid(os.getuid()).pw_name


def execute(vm, sshd, mountdir, exedir, executable, arguments):
    with contextlib.ExitStack() as stack:
        # setup reverse port forwarding to bypass NAS and firewall
        process_port = subprocess.Popen(
            [
                'ssh',
                '-oStrictHostKeyChecking=no',
                '-lroot',
                '-i' + vm.key.name,
                vm.ip,
                '-R{0}:localhost:{0}'.format(sshd.port)
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        stack.callback(process_port.terminate)

        # establish SSH connection
        client = paramiko.client.SSHClient()
        client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
        client.connect(
            vm.ip,
            username='root',
            key_filename=vm.key.name,
            look_for_keys=False
        )
        stack.callback(client.close)

        # copy over key which is required to share files
        sftp = client.open_sftp()
        stack.callback(sftp.close)
        sftp.put(sshd.key_auth.name, '.ssh/id_rsa')
        sftp.chmod('.ssh/id_rsa', 0o600)
        stack.callback(lambda: sftp.remove('.ssh/id_rsa'))

        # use the key to mount files
        mountdir = os.path.abspath(mountdir)
        sftp.mkdir('mount')
        wrap_execute(
            client,
            'sshfs -oStrictHostKeyChecking=no {0}@localhost:{2} mount -p {1}'
            .format(get_user(), sshd.port, mountdir)
        )
        stack.callback(lambda: wrap_execute(client, 'fusermount -u mount'))

        # finally execute command
        exedir = os.path.relpath(os.path.abspath(exedir), start=mountdir)
        command = \
            'cd mount/' + exedir \
            + ' &&  ' \
            + ' '.join(shlex.quote(s) for s in [executable] + arguments)
        wrap_execute(client, command)

        # test
        client.invoke_shell()


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--config', '-f',
        type=open,
        default=os.path.expanduser('~/.cloudexecrc')
    )

    parser.add_argument(
        '--basedir', '-b',
        type=str,
        default='.'
    )

    parser.add_argument(
        'executable'
    )

    parser.add_argument(
        'arguments',
        nargs=argparse.REMAINDER
    )

    return parser.parse_args()


def main():
    args = parse_args()

    with args.config:
        config = yaml.load(args.config.read())

    tmpdir = tempfile.TemporaryDirectory()
    key_ssh = Key(name=tmpdir.name + '/key.ssh')
    key_mount = Key(name=tmpdir.name + '/key.mount')
    key_local = Key(name=tmpdir.name + '/key.local')
    sshd = Sshd(
        key_host=key_local,
        key_auth=key_mount,
        workdir=tmpdir
    )

    print('Connect to provider...', end='')
    sys.stdout.flush()
    cls = get_driver(getattr(Provider, str(config['provider']).upper()))
    driver = cls(
        str(config['username']),
        str(config['api_key']),
        region=str(config['region'])
    )
    print('OK')

    vm = Vm(
        driver=driver,
        image_id=str(config['image_id']),
        size_id=str(config['size_id']),
        key=key_ssh
    )
    execute(
        vm=vm,
        sshd=sshd,
        mountdir=args.basedir,
        exedir=os.curdir,
        executable=args.executable,
        arguments=args.arguments
    )


if __name__ == '__main__':
    main()

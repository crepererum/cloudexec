import aiozmq.rpc
import asyncio
import cloudexec.common
import contextlib
import logging
import os
import paramiko
import psutil
import shlex
import socket
import subprocess


class Sshd(object):
    def __init__(self, key_host, key_auth, workdir):
        self.down = False
        self.key_auth = key_auth
        self.pidfile = workdir.name + '/sshd.pid'
        cfgpath = workdir.name + '/sshd.conf'
        logpath = workdir.name + '/sshd.log'

        logging.info('Boot sshd')

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
                    self.pidfile,
                    self.port
                )
            )

        self.process = subprocess.Popen(
            ['/usr/bin/sshd', '-f', cfgpath, '-E', logpath],
            stdout=subprocess.DEVNULL
        )
        logging.info('sshd is running')

    def __del__(self):
        self.shutdown()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.shutdown()

    def shutdown(self):
        if not self.down:
            logging.info('Shutdown sshd')
            with open(self.pidfile) as pidfile:
                cloudexec.common.shutdown_process(
                    psutil.Process(int(pidfile.readline()))
                )
            cloudexec.common.shutdown_process(self.process)
            self.down = True
            logging.info('sshd shutdown complete')

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


@asyncio.coroutine
def coro_cli(path, config, tmpdir):
    client = yield from aiozmq.rpc.connect_rpc(
        connect='ipc://{}/socket'.format(path),
        translation_table=cloudexec.common.RPC_TRANSLATION_TABLE
    )

    container = yield from client.call.get_container('default')

    key_mount = cloudexec.common.Key(name=tmpdir.name + '/key.mount')
    key_local = cloudexec.common.Key(name=tmpdir.name + '/key.local')

    with Sshd(
        key_host=key_local,
        key_auth=key_mount,
        workdir=tmpdir
    ) as sshd:
        return execute(
            container=container,
            sshd=sshd,
            mountdir=str(config['basedir']),
            exedir=os.curdir,
            executable=config['executable'],
            arguments=config['arguments']
        )


def execute(container, sshd, mountdir, exedir, executable, arguments):
    with contextlib.ExitStack() as stack:
        # setup reverse port forwarding to bypass NAS and firewall
        process_port = subprocess.Popen(
            [
                'ssh',
                '-oStrictHostKeyChecking=no',
                '-oUserKnownHostsFile=/dev/null',
                '-l' + container.user,
                '-i' + container.key,
                container.ip,
                '-R{0}:localhost:{0}'.format(sshd.port)
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        stack.callback(lambda: cloudexec.common.shutdown_process(process_port))

        # establish SSH connection
        client = paramiko.client.SSHClient()
        client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())
        client.connect(
            container.ip,
            username=container.user,
            key_filename=container.key,
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
        if 'mount' not in sftp.listdir():
            sftp.mkdir('mount')
        cloudexec.common.wrap_execute(
            client,
            'sshfs'
            ' -oStrictHostKeyChecking=no'
            ' -oUserKnownHostsFile=/dev/null'
            ' {0}@localhost:{2}'
            ' mount'
            ' -p {1}'
            .format(cloudexec.common.get_user(), sshd.port, mountdir)
        )
        stack.callback(lambda: cloudexec.common.wrap_execute(
            client,
            'fusermount -u mount'
        ))

        # finally execute command
        exedir = os.path.relpath(os.path.abspath(exedir), start=mountdir)
        command = \
            'cd mount/' + exedir \
            + ' &&  ' \
            + ' '.join(shlex.quote(s) for s in [executable] + arguments)
        return cloudexec.common.wrap_execute(client, command)

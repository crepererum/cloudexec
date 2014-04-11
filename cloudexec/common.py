import contextlib
import logging
import msgpack
import os
import os.path
import pwd
import signal
import subprocess
import sys
import xdg.BaseDirectory


class Container(object):
    def __init__(self, ip_address, user, key):
        self.ip_address = ip_address
        self.user = user
        self.key = key


class Key(object):
    def __init__(self, name):
        self.name = name
        self.name_pub = self.name + '.pub'

        logging.info('Generate key "%s"', self.name)
        subprocess.check_call(
            ['ssh-keygen', '-t', 'rsa', '-f', self.name, '-P', ''],
            stdout=subprocess.DEVNULL
        )
        logging.info('Finished key generation of "%s"', self.name)

    def __del__(self):
        try:
            os.remove(self.name)
        except FileNotFoundError:
            pass

        try:
            os.remove(self.name_pub)
        except FileNotFoundError:
            pass


class RequestException(Exception):
    def __init__(self, msg):
        Exception.__init__(self, msg)


def get_config():
    path = xdg.BaseDirectory.xdg_config_home + '/cloudexec/cloudexec.conf'
    if not os.path.isfile(path):
        path = os.path.expanduser('~/.cloudexecrc')
    return path


def get_user():
    return pwd.getpwuid(os.getuid()).pw_name


def shutdown_process(process):
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
        done = True
    except subprocess.TimeoutExpired:
        done = False

    if not done:
        process.terminate()
        try:
            process.wait(timeout=5)
            done = True
        except subprocess.TimeoutExpired:
            done = False

    if not done:
        process.kill()
        process.wait()


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


NULLPIPE = open(os.devnull, 'r+b')


RPC_ERROR_TABLE = {
    RequestException.__module__ + '.' + RequestException.__name__:
        RequestException
}


RPC_TRANSLATION_TABLE = {
    0: (
        Container,
        lambda value: msgpack.packb(
            (value.ip_address, value.user, value.key),
            use_bin_type=True
        ),
        lambda binary: Container(*msgpack.unpackb(
            binary,
            encoding='utf-8'
        ))
    )
}

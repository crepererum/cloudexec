#!/usr/bin/env python3

import aiozmq
import aiozmq.rpc
import argparse
import asyncio
import cloudexec.cli
import cloudexec.common
import cloudexec.daemon
import logging
import os
import os.path
import signal
import sys
import tempfile
import yaml


def parse_args():
    parser = argparse.ArgumentParser(
        description='Runs programs on a cloud VM',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        '--config', '-f',
        type=open,
        default=os.path.expanduser('~/.cloudexecrc'),
        help='Configuration file (YAML)'
    )

    parser.add_argument(
        '--daemon', '-d',
        action='store_true',
        default=False,
        help='Start daemon'
    )

    parser.add_argument(
        '--basedir', '-b',
        type=str,
        default='.',
        help='Basedir for file system synchronization'
    )

    parser.add_argument(
        '--profile', '-p',
        type=str,
        default='default',
        help='Profile of the requested VM as configured'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        default=False,
        help='Print more output (ignored in daemon mode)'
    )

    parser.add_argument(
        'executable',
        nargs='?',
        help='Executable or script that will executed remotely'
    )

    parser.add_argument(
        'arguments',
        nargs=argparse.REMAINDER,
        help='Arguments for your payload executable/script'
    )

    args = parser.parse_args()
    if not args.daemon and not args.executable:
        parser.print_help()
        exit(0)

    return args


def main():
    args = parse_args()

    loglevel = logging.WARNING
    if args.daemon or args.verbose:
        loglevel = logging.INFO
    logging.basicConfig(
        format='%(asctime) -15s [%(levelname)s]: %(message)s',
        level=loglevel
    )
    logging.info('Start cloudexec')

    with args.config:
        config = yaml.load(args.config.read())
    config.update(vars(args))

    path = os.path.expanduser('~/.cloudexec')
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        os.chmod(path, 0o700)

    tmpdir = tempfile.TemporaryDirectory(dir=path)
    os.chmod(tmpdir.name, 0o700)

    asyncio.set_event_loop_policy(aiozmq.ZmqEventLoopPolicy())
    loop = asyncio.get_event_loop()
    status = 0
    try:
        if config['daemon']:
            logging.info('Switch to daemon mode')
            loop.run_until_complete(cloudexec.daemon.coro_daemon(
                path=path,
                config=config,
                tmpdir=tmpdir
            ))
            logging.info('Daemon running, press CTRL-C to stop')
            loop.run_forever()
        else:
            status = loop.run_until_complete(cloudexec.cli.coro_cli(
                path=path,
                config=config,
                tmpdir=tmpdir
            ))
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        logging.info('Interrupted, waiting for shutdown')
    except cloudexec.common.RequestException as e:
        print(e, file=sys.stderr)
        status = 1
    finally:
        loop.close()

    exit(status)

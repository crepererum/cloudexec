#!/usr/bin/env python3

import aiozmq
import aiozmq.rpc
import argparse
import asyncio
import cloudexec.cli
import cloudexec.daemon
import logging
import os
import os.path
import signal
import tempfile
import yaml


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--config', '-f',
        type=open,
        default=os.path.expanduser('~/.cloudexecrc')
    )

    parser.add_argument(
        '--daemon', '-d',
        action='store_true',
        default=False,
    )

    parser.add_argument(
        '--basedir', '-b',
        type=str,
        default='.'
    )

    parser.add_argument(
        'executable',
        nargs='?'
    )

    parser.add_argument(
        'arguments',
        nargs=argparse.REMAINDER
    )

    args = parser.parse_args()
    if not args.daemon and not args.executable:
        parser.print_help()
        exit(0)

    return args


def main():
    args = parse_args()

    loglevel = logging.WARNING
    if args.daemon:
        loglevel = logging.INFO
    logging.basicConfig(
        format='%(asctime) -15s [%(levelname)s]: %(message)s',
        level=loglevel
    )
    logging.info('Start cloudexec')

    with args.config:
        config = yaml.load(args.config.read())
    config.update(vars(args))

    tmpdir = tempfile.TemporaryDirectory()
    os.chmod(tmpdir.name, 0o700)

    asyncio.set_event_loop_policy(aiozmq.ZmqEventLoopPolicy())
    loop = asyncio.get_event_loop()
    path = os.path.expanduser('~/.cloudexec')
    try:
        if config['daemon']:
            logging.info('Switch to daemon mode')
            loop.run_until_complete(cloudexec.daemon.coro_daemon(
                path=path,
                config=config,
                tmpdir=tmpdir
            ))
            logging.info('Daemon running')
            loop.run_forever()
        else:
            loop.run_until_complete(cloudexec.cli.coro_cli(
                path=path,
                config=config,
                tmpdir=tmpdir
            ))
    except KeyboardInterrupt:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        logging.info('Interrupted, waiting for shutdown')
    finally:
        loop.close()

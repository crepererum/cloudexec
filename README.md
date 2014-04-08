# cloudexec [![Build Status](https://travis-ci.org/crepererum/cloudexec.svg?branch=master)](https://travis-ci.org/crepererum/cloudexec) [![Requirements Status](https://requires.io/github/crepererum/cloudexec/requirements.png?branch=master)](https://requires.io/github/crepererum/cloudexec/requirements/?branch=master)
Sometimes you just need a bigger machine for your tasks, so why not use one? Cloudexec provides you an easy way to execute one command on remote machine which is allocated, set up and destroyed on demand. So you get power but paying a lot of money and manual setup.

## Requirements
To get this monster running you'll need:

 - Linux
 - [Python >= 3.4](https://www.python.org/)
 - Python modules mentioned in `requirements.txt` (you can use a [venv](https://docs.python.org/3.4/library/venv.html))
 - [sshd](http://www.openssh.com/) (installed, no configuration or root access required)

## Configuration
**WARNING: Please set the file permissions for the configuration file wisely (e.g. `chmod 600 ~/.cloudexecrc`). Otherwise other users might be able to read your secret API keys! Never share the configuration file or copy it to unsecure locations!**

Before you can kick some code you need to configure a cloud provider. Cloudexec reads all required data from `~/.cloudexecrc` which is a [YAML](https://en.wikipedia.org/wiki/YAML) file. The following template sets up an [Arch Linux](https://www.archlinux.org/) using [Rackspace](https://www.rackspace.com/) and a small virtual machine:

    image_id: 5345417c-23e4-4402-9243-5469cdc4730b
    size_id: 2
    username: <YOUR RACKSPACE USERNAME>
    api_key: <YOUR API KEY GOES HERE>
    region: iad
    provider: rackspace

## Usage
First of all you need to start a daemon which manages all your accounts and running VMs

    python -mcloudexec -d

To run a command in the cloud just use

    python -mcloudexec your_command --including -p -a --ram=eter s

Your current working dictionary is available so you can simply get the folder entries by

    python -mcloudexec ls -la

Even writing files is supported. Just try

    python -mcloudexec touch hello

**WARNING: The daemon tries to destroy all VMs at shutdown or when it crashes. Because there can be connection problems or other unexpected errors or even bugs in cloudexec or one of the used libraries, some VMs might live forever. Please check the dashboard of your cloud provider and kill all remaining VMs with the name `cloudexec...` to avoid high costs! The same might be true for SSH key-pairs.**


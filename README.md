# cloudexec [![Build Status](https://travis-ci.org/crepererum/cloudexec.svg?branch=master)](https://travis-ci.org/crepererum/cloudexec)
Sometimes you just need a bigger machine for your tasks, so why not use one? Cloudexec provides you an easy way to execute one command on remote machine which is allocated, set up and destroyed on demand. So you get power but paying a lot of money and manual setup.

## Requirements
To get this monster running you'll need:

 - Linux
 - Python >= 3.3
 - Python modules mentioned in `requirements.txt` (you can use a virtualenv)
 - sshd (installed, no configuration or root access required)

## Configuration
Before you can kick some code you need to configure a cloud provider. Cloudexec reads all required data from `~/.cloudexecrc` which is a YAML file. The following template sets up an Arch Linux using Rackspace and a small virtual machine:

    image_id: 5345417c-23e4-4402-9243-5469cdc4730b
    size_id: 2
    username: <YOUR RACKSPACE USERNAME>
    api_key: <YOUR API KEY GOES HERE>
    region: iad
    provider: rackspace

## Usage
To run a command in the cloud just use

    cloudexec your_command --including -p -a --ram=eter s

Your current working dictionary is available so you can simply get the folder entries by

    cloudexec ls -la


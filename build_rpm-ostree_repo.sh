#!/bin/bash

dnf install -y ostree rpm-ostree git

# rpm-ostree will throw errors about /proc and /sys not being mounted -> mount them
mount -t proc proc /proc
mount -t sysfs sys /sys

# download the standard ostree configs
git clone --branch="f39" --depth=1 https://pagure.io/workstation-ostree-config.git /tmp/workstation-ostree-config

# copy eupneaos config into cloned ostree configs
cp ./configs/fedora-eupneaos.yaml /tmp/workstation-ostree-config/workstation-ostree-config/fedora-eupneaos.yaml

mkdir -p /tmp/ostree-cache

# Initialize the OSTree repository
ostree --repo=./ init --mode=archive

# build the ostree repo
rpm-ostree compose tree --repo=./ --cachedir=/tmp/ostree-cache /tmp/workstation-ostree-config/fedora-eupneaos.yaml
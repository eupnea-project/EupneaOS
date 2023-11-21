#!/bin/bash

dnf install -y ostree rpm-ostree git

# download the standard ostree configs
git clone --branch="f39" --depth=1 https://pagure.io/workstation-ostree-config.git

# copy eupneaos config into cloned ostree configs
cp ./configs/ostree-eupneaos.yaml ./workstation-ostree-config/ostree-eupneaos.yaml

mkdir -p /mnt/rpm-ostree-repo/repo /mnt/rpm-ostree-repo/cache

# Initialize the OSTree repository
ostree --repo=/mnt/rpm-ostree-repo/repo init --mode=archive

# build the ostree repo
rpm-ostree compose tree --repo=/mnt/rpm-ostree-repo/repo --cachedir=/mnt/rpm-ostree-repo/cache ./workstation-ostree-config/ostree-eupneaos.yaml
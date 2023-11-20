#!/bin/bash

dnf install -y ostree rpm-ostree

mkdir -p /mnt/rpm-ostree-repo/repo /mnt/rpm-ostree-repo/cache

# Initialize the OSTree repository
ostree --repo=/mnt/rpm-ostree-repo/repo init --mode=archive

# build the ostree repo
rpm-ostree compose tree --repo=/mnt/rpm-ostree-repo/repo --cachedir=/mnt/rpm-ostree-repo/cache ./ostree-configs/fedora-eupneaos.yaml
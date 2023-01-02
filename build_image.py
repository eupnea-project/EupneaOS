#!/usr/bin/env python3
# This script is cloud oriented, therefore it is not very user-friendly.

import argparse
import os
import sys

from functions import *


# parse arguments from the cli. Only for testing/advanced use. All other parameters are handled by cli_input.py
def process_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", dest="dev_build", default=False, help="Use latest dev build. May be unstable.")
    parser.add_argument("--stable", dest="stable", default=False, help="Use chromeos stable kernel.")
    parser.add_argument("--exp", dest="exp", default=False, help="Use chromeos experimental 5.15 kernel.")
    parser.add_argument("--mainline-testing", dest="mainline_testing", default=False,
                        help="Use mainline testing kernel.")
    return parser.parse_args()


# Create, mount, partition the img and flash the mainline eupnea kernel
def prepare_image() -> str:
    print_status("Preparing image")

    try:
        bash(f"fallocate -l 10G eupneaos.bin")
    except subprocess.CalledProcessError:  # try fallocate, if it fails use dd
        bash(f"dd if=/dev/zero of=eupneaos.bin status=progress bs=1024 count={10 * 1000000}")
    print_status("Mounting empty image")
    img_mnt = bash("losetup -f --show eupneaos.bin")
    if img_mnt == "":
        print_error("Failed to mount image")
        exit(1)

    # partition image
    print_status("Preparing device/image partition")

    # format as per depthcharge requirements, but with a boot partition for uefi
    # READ: https://wiki.gentoo.org/wiki/Creating_bootable_media_for_depthcharge_based_devices
    bash(f"parted -s {img_mnt} mklabel gpt")
    bash(f"parted -s -a optimal {img_mnt} unit mib mkpart Kernel 1 65")  # kernel partition
    bash(f"parted -s -a optimal {img_mnt} unit mib mkpart Kernel 65 129")  # reserve kernel partition
    bash(f"parted -s -a optimal {img_mnt} unit mib mkpart ESP 129 629")  # EFI System Partition
    bash(f"parted -s -a optimal {img_mnt} unit mib mkpart Root 629 100%")  # rootfs partition
    bash(f"cgpt add -i 1 -t kernel -S 1 -T 5 -P 15 {img_mnt}")  # set kernel flags
    bash(f"cgpt add -i 2 -t kernel -S 1 -T 5 -P 1 {img_mnt}")  # set backup kernel flags
    bash(f"cgpt add -i 3 -t efi {img_mnt}")  # Set ESP type to efi

    print_status("Formatting rootfs part")
    # Format boot
    esp_mnt = img_mnt + "p3"
    bash(f"yes 2>/dev/null | mkfs.vfat -F32 {esp_mnt}")  # 2>/dev/null is to supress yes broken pipe warning
    # Create rootfs ext4 partition
    rootfs_mnt = img_mnt + "p4"
    bash(f"yes 2>/dev/null | mkfs.ext4 {rootfs_mnt}")  # 2>/dev/null is to supress yes broken pipe warning
    # Mount rootfs partition
    bash(f"mount {rootfs_mnt} /mnt/eupneaos")
    # Mount esp
    bash("mkdir -p /mnt/eupneaos/boot")
    bash(f"mount {esp_mnt} /mnt/eupneaos/boot")

    # get uuid of rootfs partition
    rootfs_partuuid = bash(f"blkid -o value -s PARTUUID {rootfs_mnt}")
    # write PARTUUID to kernel flags and save it as a file
    with open(f"configs/kernel.flags", "r") as flags:
        temp_cmdline = flags.read().replace("insert_partuuid", rootfs_partuuid).strip()
    with open("kernel.flags", "w") as config:
        config.write(temp_cmdline)

    print_status("Partitioning complete")
    flash_kernel(f"{img_mnt}p1")
    flash_kernel(f"{img_mnt}p2")  # flash reserve kernel
    return img_mnt


def flash_kernel(kernel_part: str) -> None:
    print_status("Flashing kernel to image")
    # Sign kernel
    bash("futility vbutil_kernel --arch x86_64 --version 1 --keyblock /usr/share/vboot/devkeys/kernel.keyblock"
         + " --signprivate /usr/share/vboot/devkeys/kernel_data_key.vbprivk --bootloader kernel.flags" +
         " --config kernel.flags --vmlinuz /tmp/eupneaos-build/bzImage --pack /tmp/eupneaos-build/bzImage.signed")
    bash(f"dd if=/tmp/eupneaos-build/bzImage.signed of={kernel_part}")  # part 1 is the kernel partition
    cpfile("/tmp/eupneaos-build/bzImage", "/mnt/eupneaos/boot/vmlinuz-eupnea")  # Copy kernel to /boot for uefi

    print_status("Kernel flashed successfully")


def get_uuids(img_mnt: str) -> list:
    bootpart = img_mnt + "p3"
    rootpart = img_mnt + "p4"
    bootuuid = bash(f"blkid -o value -s PARTUUID {bootpart}")
    rootuuid = bash(f"blkid -o value -s PARTUUID {rootpart}")
    return [bootuuid, rootuuid]


# Make a bootable rootfs
def bootstrap_rootfs() -> None:
    bash("tar xfp /tmp/eupneaos-build/rootfs.tar.xz -C /mnt/eupneaos --checkpoint=.10000")
    # Create a temporary resolv.conf for internet inside the chroot
    mkdir("/mnt/eupneaos/run/systemd/resolve", create_parents=True)  # dir doesnt exist coz systemd didn't run
    cpfile("/etc/resolv.conf",
           "/mnt/eupneaos/run/systemd/resolve/stub-resolv.conf")  # copy hosts resolv.conf to chroot

    # TODO: Replace generic repos with own EupneaOS repos
    chroot("dnf install --releasever=37 --allowerasing -y generic-logos generic-release generic-release-common")
    # Add eupnea repo
    chroot("dnf config-manager --add-repo https://eupnea-linux.github.io/rpm-repo/eupnea.repo")
    # Add RPMFusion repos
    chroot(f"dnf install -y https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-37.noarch.rpm")
    chroot(f"dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-37.noarch.rpm")
    chroot("dnf update --refresh -y")  # update repos
    chroot("dnf upgrade -y")  # upgrade the whole system

    # Install hardware support packages
    chroot("dnf group install -y 'Hardware Support'")
    chroot("dnf group install -y 'Common NetworkManager Submodules'")
    chroot("dnf install -y linux-firmware")
    chroot("dnf install -y eupnea-utils eupnea-system")  # install eupnea packages


def configure_rootfs() -> None:
    # Extract kernel modules
    print_status("Extracting kernel modules")
    rmdir("/mnt/eupneaos/lib/modules")  # remove all old modules
    mkdir("/mnt/eupneaos/lib/modules")
    bash(f"tar xpf /tmp/eupneaos-build/modules.tar.xz -C /mnt/eupneaos/lib/modules/ --checkpoint=.10000")
    print("")  # break line after tar

    # Enable loading modules needed for eupnea
    cpfile("configs/eupnea-modules.conf", "/mnt/eupneaos/etc/modules-load.d/eupnea-modules.conf")

    # Extract kernel headers
    print_status("Extracting kernel headers")
    dir_kernel_version = bash(f"ls /mnt/eupneaos/lib/modules/").strip()  # get modules dir name
    rmdir(f"/mnt/eupneaos/usr/src/linux-headers-{dir_kernel_version}", keep_dir=False)  # remove old headers
    mkdir(f"/mnt/eupneaos/usr/src/linux-headers-{dir_kernel_version}", create_parents=True)
    bash(f"tar xpf /tmp/eupneaos-build/headers.tar.xz -C /mnt/eupneaos/usr/src/linux-headers-{dir_kernel_version}/ "
         f"--checkpoint=.10000")
    print("")  # break line after tar
    chroot(f"ln -s /usr/src/linux-headers-{dir_kernel_version}/ "
           f"/lib/modules/{dir_kernel_version}/build")  # use chroot for correct symlink

    # copy previously downloaded firmware
    print_status("Copying google firmware")
    start_progress(force_show=True)  # start fake progress
    cpdir("linux-firmware", "/mnt/eupneaos/lib/firmware")
    stop_progress(force_show=True)  # stop fake progress

    print_status("Configuring liveuser")
    chroot("useradd --create-home --shell /bin/bash liveuser")  # add user
    chroot("usermod -aG wheel liveuser")  # add user to wheel
    chroot(f'echo "liveuser:eupneaos" | chpasswd')  # set password to eupneaos
    # set up automatic login on boot for temp-user
    with open("/mnt/eupneaos/etc/sddm.conf", "a") as sddm_conf:
        sddm_conf.write("\n[Autologin]\nUser=liveuser\nSession=plasma.desktop\n")

    # copy preset eupnea settings file for postinstall scripts
    cpfile("configs/eupnea.json", "/mnt/eupneaos/etc/eupnea.json")

    print_status("Fixing sleep")
    # disable hibernation aka S4 sleep, READ: https://eupnea-linux.github.io/main.html#/pages/bootlock
    # TODO: Fix S4 sleep
    mkdir("/mnt/eupneaos/etc/systemd/")  # just in case systemd path doesn't exist
    with open("/mnt/eupneaos/etc/systemd/sleep.conf", "a") as conf:
        conf.write("SuspendState=freeze\nHibernateState=freeze\n")

    # systemd-resolved.service needed to create /etc/resolv.conf link. Not enabled by default for some reason
    chroot("systemctl enable systemd-resolved")

    # Append lines to fstab
    with open("/mnt/eupneaos/etc/fstab", "a") as fstab:
        fstab.write(f"UUID={uuids[1]} / ext4 rw,relatime 0 1")

    # Install systemd-bootd
    # bootctl needs some paths mounted, arch-chroot does that automatically
    bash(f'arch-chroot /mnt/eupneaos bash -c "bootctl install --esp-path=/boot"')
    # Configure loader
    with open(f"configs/sysdboot-eupnea.conf", "r") as conf:
        temp_conf = conf.read().replace("insert_partuuid", uuids[1])
    with open(f"configs/sysdboot-eupnea.conf", "w") as conf:
        conf.write(temp_conf)
    cpfile("configs/sysdboot-eupnea.conf", "/mnt/eupneaos/boot/loader/entries/eupnea.conf")
    with open("/mnt/eupneaos/boot/loader/loader.conf", "w") as conf:
        conf.write("default eupnea")


def customize_kde() -> None:
    # Install KDE
    chroot("dnf group install -y 'KDE Plasma Workspaces'")
    # Set system to boot to gui
    chroot("systemctl set-default graphical.target")

    # Set kde ui settings
    print_status("Setting General UI settings")
    mkdir("/mnt/eupneaos/home/liveuser/.config")
    cpfile("configs/kde-configs/kwinrc", "/mnt/eupneaos/home/liveuser/.config/kwinrc")  # set general kwin settings
    cpfile("configs/kde-configs/kcminputrc", "/mnt/eupneaos/home/liveuser/.config/kcminputrc")  # set touchpad settings
    chroot("chown -R liveuser:liveuser /home/liveuser/.config")  # set permissions

    print_status("Installing global kde theme")
    # Installer needs to be run from within chroot
    cpdir("eupneaos-theme", "/mnt/eupneaos/tmp/eupneaos-theme")
    # run installer script for global kde theme from chroot
    bash("cd /tmp/eupneaos-theme && bash /tmp/eupneaos-theme/install.sh")

    # apply global dark theme


def relabel_files() -> None:
    # Fedora requires all files to be relabeled for SELinux to work
    # If this is not done, SELinux will prevent users from logging in
    print_status("Relabeling files for SELinux")

    # copy /proc files needed for fixfiles
    mkdir("/mnt/eupneaos/proc/self")
    cpfile("configs/selinux/mounts", "/mnt/eupneaos/proc/self/mounts")
    open("/mnt/eupneaos/proc/self/mountinfo", "w").close()  # create empty /proc/self/mountinfo

    # # copy /sys files needed for fixfiles
    # mkdir("/mnt/eupneaos/sys/fs/selinux/initial_contexts/", create_parents=True)
    # cpfile("configs/selinux/unlabeled", "/mnt/eupneaos/sys/fs/selinux/initial_contexts/unlabeled")

    # # Backup original selinux
    # cpfile("/mnt/eupneaos/usr/sbin/fixfiles", "/mnt/eupneaos/usr/sbin/fixfiles.bak")
    # # Copy patched fixfiles script
    # cpfile("configs/selinux/fixfiles", "/mnt/eupneaos/usr/sbin/fixfiles")

    chroot("/sbin/fixfiles restore")

    # Restore original fixfiles
    cpfile("/mnt/eupneaos/usr/sbin/fixfiles.bak", "/mnt/eupneaos/usr/sbin/fixfiles")
    rmfile("/mnt/eupneaos/usr/sbin/fixfiles.bak")


# Shrink image to actual size
def compress_image(img_mnt: str) -> None:
    print_status("Shrinking image")
    bash(f"e2fsck -fpv {img_mnt}p4")  # Force check filesystem for errors
    bash(f"resize2fs -f -M {img_mnt}p4")
    block_count = int(bash(f"dumpe2fs -h {img_mnt}p4 | grep 'Block count:'")[12:].split()[0])
    actual_fs_in_bytes = block_count * 4096
    # the kernel part is always the same size -> sector amount: 131072 * 512 => 67108864 bytes
    # There are 2 kernel partitions -> 67108864 bytes * 2 = 134217728 bytes
    actual_fs_in_bytes += 134217728
    # EFI partition is always the same size -> sector amount: 1024000 * 512 => 524288000 bytes
    actual_fs_in_bytes += 524288000
    actual_fs_in_bytes += 20971520  # add 20mb for linux to be able to boot properly
    bash(f"truncate --size={actual_fs_in_bytes} ./eupneaos.bin")

    # compress image to tar. Tars are smaller but the native file manager on chromeos cant uncompress them
    # These are stored as backups in the GitHub releases
    bash("tar -cv -I 'xz -9 -T0' -f ./eupneaos.bin.tar.xz ./eupneaos.bin")

    # Rar archives are bigger, but natively supported by the ChromeOS file manager
    # These are uploaded as artifacts and then manually uploaded to a cloud storage
    bash("rar a eupneaos.bin.rar -m5 eupneaos.bin")

    print_status("Calculating sha256sums")
    # Calculate sha256sum sums
    with open("eupneaos.sha256", "w") as file:
        file.write(bash("sha256sum eupneaos.bin eupneaos.bin.tar.xz "
                        "eupneaos.bin.rar"))


def chroot(command: str) -> None:
    bash(f'chroot /mnt/eupneaos /bin/bash -c "{command}"')  # always print output


if __name__ == "__main__":
    args = process_args()  # process args
    set_verbose(True)  # increase verbosity

    # parse arguments
    kernel_type = "mainline"
    if args.dev_build:
        print_warning("Using dev release")
    if args.exp:
        print_warning("Using experimental chromeos kernel")
        kernel_type = "exp"
    if args.stable:
        print_warning("Using stable chromes kernel")
        kernel_type = "stable"
    if args.mainline_testing:
        print_warning("Using mainline testing kernel")
        kernel_type = "mainline-testing"

    # prepare mount
    mkdir("/mnt/eupneaos", create_parents=True)

    image_props = prepare_image()
    uuids = get_uuids(image_props)

    # # Bind mount directories
    # print_status("Bind-mounting directories")
    # mkdir("/mnt/eupneaos/dev")
    # bash("mount --rbind /dev /mnt/eupneaos/dev")
    # bash("mount --make-rslave /mnt/eupneaos/dev")

    bootstrap_rootfs()
    configure_rootfs()
    customize_kde()

    # unmount boot before relabeling
    bash("umount -f /mnt/eupneaos/boot")
    relabel_files()

    # Clean image of temporary files
    rmdir("/mnt/eupneaos/tmp")
    rmdir("/mnt/eupneaos/var/tmp")
    rmdir("/mnt/eupneaos/var/cache")
    rmdir("/mnt/eupneaos/proc")
    rmdir("/mnt/eupneaos/run")
    rmdir("/mnt/eupneaos/sys")
    rmdir("/mnt/eupneaos/lost+found")
    rmdir("/mnt/eupneaos/dev")
    rmfile("/mnt/eupneaos/.stop_progress")

    bash("sync")  # write all pending changes to image

    # Force unmount image
    bash("umount -f /mnt/eupneaos")
    sleep(5)  # wait for umount to finish
    compress_image(image_props)

    bash(f"losetup -d {image_props}")  # unmount image

    print_header("Image creation completed successfully!")

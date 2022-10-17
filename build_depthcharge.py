#!/usr/bin/env python3
# This script is cloud oriented, so it is not very user-friendly.

import sys
import os
import argparse
from typing import Tuple

from functions import *


# parse arguments from the cli. Only for testing/advanced use. All other parameters are handled by cli_input.py
def process_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true", dest="verbose", default=False,
                        help="Print more output")
    parser.add_argument("--dev", action="store_true", dest="dev_build", default=False,
                        help="Use latest dev build. May be unstable.")
    parser.add_argument("--alt", action="store_true", dest="alt", default=False,
                        help="Use alt kernel. Only for older devices.")
    parser.add_argument("--exp", action="store_true", dest="exp", default=False,
                        help="Use experimental 5.15 kernel.")
    parser.add_argument("--mainline", action="store_true", dest="mainline", default=False,
                        help="Use mainline kernel instead of modified chromeos kernel.")
    return parser.parse_args()


# Create, mount, partition the img and flash the eupnea kernel
def prepare_image() -> Tuple[str, str]:
    print_status("Preparing image")

    bash(f"fallocate -l 8G eupnea-depthcharge.bin")
    print_status("Mounting empty image")
    img_mnt = bash("losetup -f --show eupnea-depthcharge.bin")
    if img_mnt == "":
        print_error("Failed to mount image")
        exit(1)

    # partition image
    print_status("Preparing device/image partition")

    # format image as per depthcharge requirements
    # READ: https://wiki.gentoo.org/wiki/Creating_bootable_media_for_depthcharge_based_devices
    bash(f"parted -s {img_mnt} mklabel gpt")
    bash(f"parted -s -a optimal {img_mnt} unit mib mkpart Kernel 1 65")  # kernel partition
    bash(f"parted -s -a optimal {img_mnt} unit mib mkpart Root 65 100%")  # rootfs partition
    bash(f"cgpt add -i 1 -t kernel -S 1 -T 5 -P 15 {img_mnt}")  # depthcharge flags

    print_status("Formatting rootfs part")
    rootfs_mnt = img_mnt + "p2"  # second partition is rootfs
    # Create rootfs ext4 partition
    bash(f"yes 2>/dev/null | mkfs.ext4 {rootfs_mnt}")  # 2>/dev/null is to supress yes broken pipe warning
    # Mount rootfs partition
    bash(f"mount {rootfs_mnt} /mnt/eupnea")

    # get uuid of rootfs partition
    rootfs_partuuid = bash(f"blkid -o value -s PARTUUID {rootfs_mnt}")
    # write PARTUUID to kernel flags and save it as a file
    with open(f"configs/kernel.flags", "r") as flags:
        temp_cmdline = flags.read().replace("insert_partuuid", rootfs_partuuid).strip()
    # SELinux is temporarily disabled, until we can figure out how to relabel files without rebooting
    with open("kernel.flags", "w") as config:
        config.write(temp_cmdline)

    print_status("Partitioning complete")
    flash_kernel(f"{img_mnt}p1")
    return rootfs_partuuid, img_mnt


def flash_kernel(kernel_part: str) -> None:
    print_status("Flashing kernel to device/image")
    # Sign kernel
    bash("futility vbutil_kernel --arch x86_64 --version 1 --keyblock /usr/share/vboot/devkeys/kernel.keyblock"
         + " --signprivate /usr/share/vboot/devkeys/kernel_data_key.vbprivk --bootloader kernel.flags" +
         " --config kernel.flags --vmlinuz /tmp/eupnea-build/bzImage --pack /tmp/eupnea-build/bzImage.signed")
    bash(f"dd if=/tmp/eupnea-build/bzImage.signed of={kernel_part}")  # part 1 is the kernel partition

    print_status("Kernel flashed successfully")


# Make a bootable rootfs
def bootstrap_rootfs(root_partuuid) -> None:
    bash("dnf -y --releasever=36 --installroot=/mnt/eupnea groupinstall core")

    # Create a temporary resolv.conf for internet inside the chroot
    mkdir("/mnt/eupnea/run/systemd/resolve", create_parents=True)  # dir doesnt exist coz systemd didnt run
    cpfile("/etc/resolv.conf", "/mnt/eupnea/run/systemd/resolve/stub-resolv.conf")  # copy hosts resolv.conf to chroot

    # TODO: Replace generic repos with own eupnea repos
    chroot("dnf install  --releasever=36 --allowerasing -y generic-logos generic-release generic-release-common")
    chroot("dnf group install -y 'Common NetworkManager Submodules'")
    chroot("dnf group install -y 'Hardware Support'")
    chroot("dnf install -y linux-firmware")

    # Add RPMFusion repos
    chroot(f"dnf install -y https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-36.noarch.rpm")
    chroot(f"dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-36.noarch.rpm")


def configure_rootfs() -> None:
    # Extract kernel modules
    print_status("Extracting kernel modules")
    rmdir("/mnt/eupnea/lib/modules")  # remove all old modules
    mkdir("/mnt/eupnea/lib/modules")
    bash(f"tar xpf /tmp/eupnea-build/modules.tar.xz -C /mnt/eupnea/lib/modules/ --checkpoint=.10000")
    print("")  # break line after tar

    # Enable loading modules needed for eupnea
    cpfile("configs/eupnea-modules.conf", "/mnt/eupnea/etc/modules-load.d/eupnea-modules.conf")

    # Extract kernel headers
    print_status("Extracting kernel headers")
    dir_kernel_version = bash(f"ls /mnt/eupnea/lib/modules/").strip()  # get modules dir name
    rmdir(f"/mnt/eupnea/usr/src/linux-headers-{dir_kernel_version}", keep_dir=False)  # remove old headers
    mkdir(f"/mnt/eupnea/usr/src/linux-headers-{dir_kernel_version}", create_parents=True)
    bash(f"tar xpf /tmp/eupnea-build/headers.tar.xz -C /mnt/eupnea/usr/src/linux-headers-{dir_kernel_version}/ "
         f"--checkpoint=.10000")
    print("")  # break line after tar
    chroot(f"ln -s /usr/src/linux-headers-{dir_kernel_version}/ "
           f"/lib/modules/{dir_kernel_version}/build")  # use chroot for correct symlink

    # Copy chromebook firmware
    print_status("Copying google firmware")
    rmdir("/mnt/eupnea/lib/firmware")
    cpdir("linux-firmware", "/mnt/eupnea/lib/firmware")

    # Set device hostname
    with open("/mnt/eupnea/etc/hostname", "w") as hostname_file:
        hostname_file.write("eupnea-chromebook" + "\n")

    print_status("Configuring liveuser")
    chroot("useradd --create-home --shell /bin/bash liveuser")  # add user
    chroot("usermod -aG wheel liveuser")  # add user to wheel
    chroot(f'echo "liveuser:" | chpasswd')  # set password to blank
    # set up automatic login on boot for temp-user
    with open("/mnt/eupnea/etc/sddm.conf", "a") as sddm_conf:
        sddm_conf.write("\n[Autologin]\nUser=liveuser\nSession=plasma.desktop\n")

    print_status("Copying eupnea scripts and configs")
    # Copy postinstall scripts
    for file in Path("postinstall-scripts").iterdir():
        if file.is_file():
            if file.name == "LICENSE" or file.name == "README.md" or file.name == ".gitignore":
                continue  # dont copy license, readme and gitignore
            else:
                cpfile(file.absolute().as_posix(), f"/mnt/eupnea/usr/local/bin/{file.name}")

    # copy audio setup script
    cpfile("audio-scripts/setup-audio", "/mnt/eupnea/usr/local/bin/setup-audio")

    # copy functions file
    cpfile("functions.py", "/mnt/eupnea/usr/local/bin/functions.py")
    chroot("chmod 755 /usr/local/bin/*")  # make scripts executable in system

    # copy configs
    mkdir("/mnt/eupnea/etc/eupnea")
    cpdir("configs", "/mnt/eupnea/etc/eupnea")  # eupnea-builder configs
    cpdir("postinstall-scripts/configs", "/mnt/eupnea/etc/eupnea")  # postinstall configs
    cpdir("audio-scripts/configs", "/mnt/eupnea/etc/eupnea")  # audio configs

    # copy preset eupnea settings file for postinstall scripts to read
    cpfile("configs/eupnea.json", "/mnt/eupnea/etc/eupnea.json")

    # Add postinstall service hook
    print_status("Adding postinstall service")
    cpfile("configs/postinstall.service", "/mnt/eupnea/etc/systemd/system/postinstall.service")
    chroot("systemctl enable postinstall.service")

    print_status("Fixing sleep")
    # disable hibernation aka S4 sleep, READ: https://eupnea-linux.github.io/docs.html#/pages/bootlock
    # TODO: Fix S4 sleep
    mkdir("/mnt/eupnea/etc/systemd/")  # just in case systemd path doesn't exist
    with open("/mnt/eupnea/etc/systemd/sleep.conf", "a") as conf:
        conf.write("SuspendState=freeze\nHibernateState=freeze\n")

    # TODO: Fix failing services
    # The services below fail to start, so they are disabled
    # ssh
    rmfile("/mnt/eupnea/etc/systemd/system/multi-user.target.wants/ssh.service")
    rmfile("/mnt/eupnea/etc/systemd/system/sshd.service")
    # TODO: Fix zram
    chroot("dnf remove zram-generator-defaults -y")  # remove zram as it fails for some reason
    chroot("systemctl disable systemd-zram-setup@zram0.service")  # disable zram service

    # The default fstab file has the wrong PARTUUID -> system boots in emergency mode if not fixed
    # with open("configs/fstab.txt", "r") as f:
    #     fstab = f.read()
    # fstab = fstab.replace("insert_partuuid", root_partuuid)
    # with open("/mnt/eupnea/etc/fstab", "w") as f:
    #     f.write(fstab)


def customize_kde() -> None:
    # Install KDE
    chroot("dnf group install -y 'KDE Plasma Workspaces'")
    # Set system to boot to gui
    chroot("systemctl set-default graphical.target")
    # Add chromebook keyboard layout. Needs to be done after install Xorg
    print_status("Backing up default keymap and setting Chromebook layout")
    cpfile("/mnt/eupnea/usr/share/X11/xkb/symbols/pc", "/mnt/eupnea/usr/share/X11/xkb/symbols/pc.default")
    cpfile("configs/xkb/xkb.chromebook", "/mnt/eupnea/usr/share/X11/xkb/symbols/pc")

    # Set kde ui settings
    print_status("Setting General UI settings")
    mkdir("/mnt/eupnea/home/liveuser/.config")
    cpfile("configs/kde-configs/kwinrc", "/mnt/eupnea/home/liveuser/.config/kwinrc")  # set general kwin settings
    cpfile("configs/kde-configs/kcminputrc", "/mnt/eupnea/home/liveuser/.config/kcminputrc")  # set touchpad settings
    chroot("chown -R liveuser:liveuser /home/liveuser/.config")  # set permissions

    print_status("Installing global kde theme")
    # Installer needs to be run from within chroot
    cpdir("eupnea-theme", "/mnt/eupnea/tmp/eupnea-theme")
    # run installer script from chroot
    chroot("cd /tmp/eupnea-theme && python3 /tmp/eupnea-theme/install.py")  # install global theme
    chroot("bash /tmp/eupnea-theme/sddm/install.sh")  # install login theme
    rmdir("/mnt/eupnea/tmp/eupnea-theme")  # remove theme repo, to reduce image size


def compress_image(img_mnt: str) -> None:
    print_status("Shrinking image")

    # Shrink image to actual size
    bash(f"e2fsck -fpv {img_mnt}p2")  # Force check filesystem for errors
    bash(f"resize2fs -M {img_mnt}p2")
    block_count = int(bash(f"dumpe2fs -h {img_mnt}p2 | grep 'Block count:'")[12:].split()[0])
    actual_fs_in_bytes = block_count * 4096
    # the kernel part is always the same size -> sector amount: 131072 * 512 => 67108864 bytes
    actual_fs_in_bytes += 67108864
    actual_fs_in_bytes += 102400  # add 100kb for linux to be able to boot
    bash(f"truncate --size={actual_fs_in_bytes} ./eupnea-depthcharge.bin")

    # compress image to tar. Tars are smaller but the native file manager on chromeos cant uncompress them
    bash("tar -cv -I 'xz -9 -T0' -f ./eupnea-depthcharge.bin.tar.xz ./eupnea-depthcharge.bin")

    # Rars are bigger but natively supported by the ChromeOS file manager
    bash("rar a eupnea-depthcharge.bin.rar -m5 eupnea-depthcharge.bin")


def chroot(command: str) -> None:
    if args.verbose:
        bash(f'chroot /mnt/eupnea /bin/sh -c "{command}"')
    else:
        bash(f'chroot /mnt/eupnea /bin/sh -c "{command}" 2>/dev/null 1>/dev/null')  # supress all output


if __name__ == "__main__":
    if os.geteuid() == 0 and not path_exists("/tmp/.eupnea_root_ok"):
        print_error("Please start the script as non-root/without sudo")
        exit(1)

    args = process_args()  # process args before elevating to root for better ux

    # Restart script as root
    if not os.geteuid() == 0:
        # create empty file to confirm script was started as non-root
        with open("/tmp/.eupnea_root_ok", "w") as file:
            file.write("")
        sudo_args = ['sudo', sys.executable] + sys.argv + [os.environ]
        os.execlpe('sudo', *sudo_args)

    # delete file to confirm script was started as root
    rmfile("/tmp/.eupnea_root_ok")

    # parse arguments
    dev_release = args.dev_build
    kernel_type = "stable"
    if args.dev_build:
        print_warning("Using dev release")
    if args.alt:
        print_warning("Using alt kernel")
        kernel_type = "alt"
    if args.exp:
        print_warning("Using experimental kernel")
        kernel_type = "exp"
    if args.mainline:
        print_warning("Using mainline kernel")
        kernel_type = "mainline"
    if args.verbose:
        print_warning("Verbosity increased")
        set_verbose(True)  # set verbose in functions.py

    # Check that required packages are installed and yum repos are present
    if not path_exists("/usr/bin/dnf") and not path_exists("/etc/yum.repos.d/"):
        print_error("Install dnf and add yum repos!")
        exit(1)

    # prepare mount
    mkdir("/mnt/eupnea", create_parents=True)

    image_props = prepare_image()
    bootstrap_rootfs(image_props[0])
    configure_rootfs()
    customize_kde()

    # Unmount image to prevent tar error: "file changed as we read it"
    bash("umount -f /mnt/eupnea")
    sleep(5)  # wait for umount to finish
    compress_image(image_props[1])

    bash(f"losetup -d {image_props[1]}")  # unmount image

    print_header("Image creation completed successfully!")

#!/usr/bin/env python3
# This script is cloud oriented, so it is not very user-friendly.

import sys
import os
import argparse
from time import perf_counter

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
    if args.local_path:
        print_warning("Using local files")
    if args.verbose:
        print_warning("Verbosity increased")

    # Check that required packages are installed and yum repos are present
    # if not path_exists("/usr/bin/dnf") and not path_exists("/etc/yum.repos.d/"):
    #     print_error("Install dnf and add yum repos!")
    #     exit(1)

    # prepare_image()
    # flash_kernel()
    # bootstrap_fedora()
    # replace_licensed_files()
    # apply_eupnea_patches()
    # install_and_customize_kde()

    print_header("Image creation completed successfully!")

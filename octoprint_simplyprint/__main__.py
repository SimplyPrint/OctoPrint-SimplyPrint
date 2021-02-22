import argparse


def run_script(name):
    # Currently there's only one script, but it could change :)
    if name == "run_healthcheck":
        from octoprint_simplyprint.local.background import run_background_check
        run_background_check()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SimplyPrint Local Scripts")
    parser.add_argument("script",
                        default=None,
                        help="The script you want to call, currently only `run_healthcheck` is valid")
    args = parser.parse_args()
    run_script(args.script)

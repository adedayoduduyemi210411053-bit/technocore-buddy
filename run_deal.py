import subprocess
import sys
import shutil

def main():
    if not shutil.which("node"):
        print("? Node.js is required to run the tclk protocol engine.")
        print("Please install Node.js from https://nodejs.org/")
        sys.exit(1)
        
    print("?? Executing Flop Labs tclk Agent Deal Protocol...\n")
    try:
        subprocess.run(["node", "tclk_contract.js"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n? Deal execution failed with exit code: {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()

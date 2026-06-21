import subprocess
import time
import sys
import os
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def _prompt_and_verify_vault_password():
    """Prompt once for the vault password and verify it decrypts the vault.

    Returns the verified password so the wrapper can pipe it to each restarted
    bot.py child on stdin (POLYBOT_VAULT_PASSWORD_STDIN=1), so the operator is
    not re-prompted on every 90-minute auto-restart. Re-prompts on a malformed
    or wrong password; structural vault errors (missing/corrupt/insecure file)
    propagate since re-typing cannot fix them. The password is held only in
    this wrapper's memory for the session and never written to env or disk.
    """
    from getpass import getpass

    from vault_crypto import InvalidVaultPasswordError
    from vault_store import (
        DEFAULT_VAULT_FILE,
        load_vault,
        validate_vault_password,
    )

    while True:
        try:
            password = validate_vault_password(
                getpass("Credentials vault password: ")
            )
        except ValueError as exc:
            # Password FORMAT problem (empty / surrounding whitespace / too
            # short) — retryable by re-typing.
            print(f"Vault password rejected: {exc}. Please try again.")
            continue
        try:
            # Verify the password actually decrypts the vault; discard the
            # decrypted result so secrets do not persist in the wrapper. ONLY a
            # wrong password is retryable here; every other vault error
            # (missing / corrupt / insecure file, or any decrypted-but-invalid
            # payload — whatever exception type it surfaces as) propagates,
            # since re-typing the password cannot fix it.
            load_vault(password, DEFAULT_VAULT_FILE)
        except InvalidVaultPasswordError:
            print("Vault password incorrect. Please try again.")
            continue
        return password


def run_bot():
    """Run the bot with auto-restart using the SAME Python environment."""
    
    BOT_SCRIPT = "bot.py"
    
    # CRITICAL: Use the SAME Python executable
    python_cmd = sys.executable
    
    # Get command line arguments (excluding the script name)
    # If you run "python 15m_bot_runner.py --live --confirm-live", this
    # captures ['--live', '--confirm-live'].
    bot_args = sys.argv[1:] if len(sys.argv) > 1 else []
    is_live = "--live" in bot_args
    
    print("=" * 80)
    print("BTC 15-MIN TRADING BOT - AUTO-RESTART WRAPPER")
    print("=" * 80)
    print(f"Platform: {sys.platform}")
    print(f"Python: {python_cmd}")
    print(f"Bot script: {BOT_SCRIPT}")
    print(f"Bot arguments: {bot_args}")
    print(f"Virtual env: {sys.prefix}")
    print("=" * 80)
    print()
    
    # Check if bot script exists
    if not os.path.exists(BOT_SCRIPT):
        print(f"ERROR: Bot script '{BOT_SCRIPT}' not found!")
        print(f"Current directory: {os.getcwd()}")
        print(f"Files in directory: {os.listdir('.')}")
        print()
        print("Available .py files:")
        for file in os.listdir('.'):
            if file.endswith('.py'):
                print(f"  - {file}")
        print()
        print("Please set BOT_SCRIPT to your bot filename")
        sys.exit(1)
    
    # In live mode, prompt for the vault password ONCE here in the supervising
    # wrapper, verify it, then pipe it to each restarted child on stdin so the
    # operator is not asked again on every 90-minute auto-restart. The password
    # is held only in this process's memory; the flag below is not a secret.
    child_env = os.environ.copy()
    vault_password = None
    if is_live:
        # The wrapper pipes exactly one stdin line (the vault password) to each
        # child, so bot.py MUST run with --confirm-live to skip its interactive
        # "Type LIVE" prompt; otherwise that input() would consume the piped
        # password line and the child would abort on every restart. The wrapper
        # is inherently unattended after this single prompt, so we require
        # --confirm-live explicitly rather than silently injecting it.
        if "--confirm-live" not in bot_args:
            sys.exit(
                "Live auto-restart wrapper requires --confirm-live: it prompts "
                "for the vault password once and pipes it to each restarted "
                "child, which needs --confirm-live to skip the interactive LIVE "
                "prompt. Re-run: python 15m_bot_runner.py --live --confirm-live"
            )
        vault_password = _prompt_and_verify_vault_password()
        child_env["POLYBOT_VAULT_PASSWORD_STDIN"] = "1"

    restart_count = 0

    while True:
        restart_count += 1
        
        print("=" * 80)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        print(f"Starting bot (restart #{restart_count})...")
        print(f"Command: {python_cmd} {BOT_SCRIPT} {' '.join(bot_args)}")
        print("=" * 80)
        print()
        
        try:
            # Run the bot with arguments!
            cmd = [python_cmd, BOT_SCRIPT] + bot_args
            if is_live:
                # Feed the vault password on the child's stdin (memory -> pipe,
                # never env or disk). subprocess closes stdin after the line, so
                # the child reads exactly one password line, then sees EOF.
                result = subprocess.run(
                    cmd,
                    check=False,
                    env=child_env,
                    input=vault_password + "\n",
                    text=True,
                )
            else:
                result = subprocess.run(cmd, check=False, env=child_env)
            
            exit_code = result.returncode
            
            print()
            print("=" * 80)
            print(f"Bot stopped at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Exit code: {exit_code}")
            print("=" * 80)
            
            # Normal termination (auto-restart from bot)
            if exit_code in [0, 143, 15, -15]:
                print("✅ Normal auto-restart - loading fresh filters...")
                wait_time = 2
            else:
                print(f"⚠️ Error detected (code {exit_code}) - waiting before retry...")
                wait_time = 10
            
            print(f"Restarting in {wait_time} seconds...")
            print()
            time.sleep(wait_time)
            
        except KeyboardInterrupt:
            print()
            print("=" * 80)
            print("Keyboard interrupt received - stopping wrapper")
            print("=" * 80)
            break
            
        except Exception as e:
            print()
            print("=" * 80)
            print(f"ERROR running bot: {e}")
            print("=" * 80)
            print("Waiting 10 seconds before retry...")
            print()
            time.sleep(10)

if __name__ == "__main__":
    try:
        run_bot()
    except KeyboardInterrupt:
        print("\nStopped by user")
        sys.exit(0)
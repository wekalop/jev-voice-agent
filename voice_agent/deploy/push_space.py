"""Creates or updates the Hugging Face Space: python -m deploy.push_space your-username/nabda-voice-test

Reads HF_TOKEN and the service keys from .env, and stores those keys as encrypted Space secrets.
Prints no key values. The Space's code and fictional data are public. Calls need the password.
"""

import secrets
import sys

from dotenv import dotenv_values
from huggingface_hub import HfApi

from deploy.build_space import ROOT, build

SECRET_NAMES = ["JEV_API_KEY", "GROQ_API_KEY", "CARTESIA_API_KEY", "ACCESS_PASSWORD"]
RELAY_NAMES = ["TURNIX_API_TOKEN", "TURN_KEY_ID", "TURN_KEY_API_TOKEN"]
SETTING_NAMES = ["JEV_MODEL", "JEV_BASE_URL", "JEV_MODE", "LLM_MODEL", "LLM_FALLBACK_MODEL", "CLASSIFIER_MODEL", "TTS_MODEL", "TTS_VOICE"]


def main() -> None:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        raise SystemExit("Usage: python -m deploy.push_space your-username/nabda-voice-test")
    space = sys.argv[1]
    env_path = ROOT / ".env"
    env = dotenv_values(env_path)

    if not env.get("ACCESS_PASSWORD"):
        env["ACCESS_PASSWORD"] = secrets.token_urlsafe(12)
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\nACCESS_PASSWORD={env['ACCESS_PASSWORD']}\n")
        print("Created a password and saved it to .env as ACCESS_PASSWORD.")

    missing = [name for name in ["HF_TOKEN", *SECRET_NAMES] if not env.get(name)]
    if not env.get("TURNIX_API_TOKEN") and not (env.get("TURN_KEY_ID") and env.get("TURN_KEY_API_TOKEN")):
        missing.append("TURNIX_API_TOKEN")
    if missing:
        raise SystemExit(f"Add these to .env first: {', '.join(missing)}")

    folder = build()
    api = HfApi(token=env["HF_TOKEN"])
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
    for name in SECRET_NAMES + [name for name in RELAY_NAMES if env.get(name)]:
        api.add_space_secret(space, name, env[name])
    for name in SETTING_NAMES:
        if env.get(name):
            api.add_space_variable(space, name, env[name])
    api.upload_folder(repo_id=space, repo_type="space", folder_path=str(folder), commit_message="Deploy voice agent")
    owner, name = space.split("/")
    print(f"Uploaded. Build status: https://huggingface.co/spaces/{space}")
    print(f"Link to share, once it is running: https://{owner}-{name}.hf.space".lower())


if __name__ == "__main__":
    main()

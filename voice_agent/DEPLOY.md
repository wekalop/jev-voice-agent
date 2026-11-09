# Sharing the voice agent

The agent is live at https://nabda-voice.hassan-medhat26.workers.dev and runs with your PC off. It's the Cloudflare Workers version in `edge/`, and `edge/README.md` explains how to publish and test it. Send the link, and send the password from `.env` (`ACCESS_PASSWORD`) separately.

The rest of this page covers the older Python version, which runs on your PC. It uses a Cloudflare quick tunnel for a temporary public link and the Turnix relay for call audio.

## Start a demo

Keep your PC awake and leave both windows open during the demo. Run these from the `voice_agent` folder.

In the first window, start the agent:

```bash
.venv/Scripts/python -m live.server
```

In the second window, start the link:

```bash
"/c/Program Files (x86)/cloudflared/cloudflared.exe" tunnel --no-autoupdate --url http://localhost:7860
```

The second window prints a `https://....trycloudflare.com` address. It changes each time you restart the tunnel.

## Share it

- Send the link, and send the password from `.env` (`ACCESS_PASSWORD`) separately.
- Ask them to use Chrome or Edge and allow the microphone.
- Make a test call from another network, such as a phone hotspot, before the demo.

To stop sharing, close the tunnel window. To change the password, edit `ACCESS_PASSWORD` in `.env` and restart the agent.

## Limits

- Your PC must stay on. Cloudflare says quick tunnels are for testing and have no uptime guarantee.
- One caller at a time, and calls end after ten minutes (`MAX_CALL_SECONDS`).
- Turnix's free plan includes 10 GB a month. Its encrypted relay had an expired certificate in November 2026, so the agent uses its plain connection. Call audio still has its own encryption.
- Groq's free limit can switch replies to the fallback model.

## Staying online without your PC

Hugging Face's free tier refused this app in November 2026: Docker Spaces need a PRO subscription. If you subscribe, this uploads the agent to a Space:

```bash
.venv/Scripts/python -m deploy.push_space your-username/nabda-voice-test
```

It reads `HF_TOKEN` and the service keys from `.env`, and stores them as encrypted Space secrets. The Space's code and fictional data are public, and calls need the password. This route hasn't been tested end to end.

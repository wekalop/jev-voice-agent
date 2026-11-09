# Nabda voice agent on Cloudflare Workers

This version runs with your PC off. The browser detects speech and plays audio, and the Worker calls Whisper, Jev, the reply model, and Cartesia while keeping the keys private.

## Publish

Sign in once:

```bash
npx wrangler login
```

Then, from this folder:

```bash
npm run build
```

```bash
npx wrangler deploy
```

```bash
node scripts/sync_secrets.mjs remote
```

Until the secrets are stored, the Worker only shows "isn't set up". The keys come from `../.env`. Send the link and the `ACCESS_PASSWORD` from `.env` separately.

The live address is https://nabda-voice.hassan-medhat26.workers.dev. To see what a live call is doing, run `npx wrangler tail nabda-voice`.

## Test

```bash
npm test
```

## How it differs from the Python agent

- The call goes over ordinary HTTPS, so it needs no WebRTC or relay.
- A call's state travels in an encrypted token, so the Worker stores nothing between turns.
- The `two_call_parallel` mode isn't included.
- Timings are measured in the browser, so they include the network. They're shown on the page, not saved to a log.

## Limits

- Workers' free plan allows 100,000 requests a day and 10 ms of CPU per request.
- In live tests on 4 November 2026, turns reported up to 24 ms of CPU, above that 10 ms limit, and none was stopped. Cloudflare could still enforce the limit, which would show as Error 1102. The paid plan removes this risk.
- Several people can call at once, but they share Groq's free limit of about 8,000 tokens a minute per model. In the same tests, 3 of 12 turns sent about 13 seconds apart got the "free usage limit" message.
- The first call downloads about 17 MB for the voice detector. After that, the browser usually reuses it.
- There's no limit on password guesses, so the password needs to stay long and random.

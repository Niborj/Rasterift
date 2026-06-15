# Rasterift Render Deployment

Rasterift is deployed as a Docker web service. The production entry point is `server.py`; it imports the existing FastAPI app from `stream_server.py`, initializes app state without stdin, and runs Uvicorn on `0.0.0.0:$PORT`.

References:

- Render Blueprint spec: https://render.com/docs/blueprint-spec
- Render web services: https://render.com/docs/web-services
- Render custom domains: https://render.com/docs/custom-domains
- Render DNS records: https://render.com/docs/configure-other-dns
- Squarespace DNS records: https://support.squarespace.com/hc/en-us/articles/360002101888-Adding-DNS-records-to-your-domain
- Squarespace domain pointing: https://support.squarespace.com/hc/en-us/articles/215744668-Pointing-a-Squarespace-domain

## Local Docker Check

Build the image:

```bash
docker build -t rasterift-render .
```

Run it with the same data directory and port style Render uses:

```bash
docker run --rm \
  -e PORT=10000 \
  -e RASTERIFT_DATA_DIR=/data \
  -p 10000:10000 \
  rasterift-render
```

Check health and the homepage:

```bash
curl http://localhost:10000/health
curl -I http://localhost:10000/
```

Expected health response:

```json
{"status":"ok"}
```

## Deploy On Render With `render.yaml`

1. Push this repository to GitHub or GitLab with `Dockerfile`, `server.py`, `render.yaml`, and `requirements.txt` at the repository root.
2. In Render, choose **New +** then **Blueprint**.
3. Connect the repository and select the branch to deploy.
4. Render reads `render.yaml` and creates the `rasterift` Docker web service.
5. Confirm the service has environment variable `RASTERIFT_DATA_DIR=/data`.
6. Deploy the service.
7. Open the service URL after deploy, for example `https://rasterift.onrender.com`.
8. Confirm `https://YOUR-RENDER-SERVICE.onrender.com/health` returns `{"status":"ok"}`.
9. Upload a video from the Rasterift UI. New deploys start with an empty library unless you deliberately add a bundled default video.

Optional: set `RASTERIFT_DEFAULT_VIDEO` to a path inside the image or mounted data directory if you want Rasterift to pre-load a default source video. Leave it unset for the normal Render upload-first flow.

Render will build from `Dockerfile`, install FFmpeg and OpenCV runtime libraries, install `requirements.txt`, and start the container with:

```bash
python server.py
```

## Persistent Uploads On Render

The Docker image creates `/data/uploads` and `/data/source_cache`. Without a Render persistent disk, those files are container-local and can disappear on redeploy or restart.

For durable uploads, add a Render persistent disk mounted at:

```text
/data
```

Keep `RASTERIFT_DATA_DIR=/data` unchanged.

## Squarespace DNS For A Render Domain

Do this after the Render service is live.

1. In Render, open the Rasterift web service.
2. Go to **Settings** then **Custom Domains**.
3. Click **Add Custom Domain**.
4. Add the exact domain you want to use, such as `rasterift.example.com`, `www.example.com`, or `example.com`.
5. Save it and keep the Render DNS target visible. It will look like `YOUR-RENDER-SERVICE.onrender.com`.

### Point A Squarespace Subdomain

Use this for `rasterift.example.com`, `demo.example.com`, or `www.example.com`.

1. Open the Squarespace domains dashboard.
2. Select the root domain.
3. Open **DNS** then **DNS Settings**.
4. Scroll to **Custom Records** and choose **Add Record**.
5. Set **Type** to `CNAME`.
6. Set **Host** to the subdomain only:
   - `rasterift` for `rasterift.example.com`
   - `demo` for `demo.example.com`
   - `www` for `www.example.com`
7. Set **Alias Data** or **Data** to the Render hostname, for example:

```text
YOUR-RENDER-SERVICE.onrender.com
```

8. Save the record.
9. Remove any conflicting record with the same host.
10. In Render, click **Verify** next to the custom domain.

### Point A Squarespace Root Domain

Use this for `example.com`.

1. In Squarespace, open the domain's **DNS Settings**.
2. Remove Squarespace default website records if Squarespace says they conflict with pointing the domain elsewhere.
3. Remove any `AAAA` records for the root domain while configuring Render.
4. Prefer an `ALIAS` record if Squarespace offers it:
   - **Type:** `ALIAS`
   - **Host:** `@`
   - **Alias Data/Data:** `YOUR-RENDER-SERVICE.onrender.com`
5. If `ALIAS` is unavailable, use Render's fallback A record:
   - **Type:** `A`
   - **Host:** `@`
   - **IP Address:** `216.24.57.1`
6. Save the record.
7. In Render, click **Verify** next to the custom domain.

DNS can take minutes to propagate, and Squarespace notes that changes can take 24 to 48 hours. Once Render verifies the domain, it provisions TLS automatically and Rasterift WebSockets use `wss://` on the same host.

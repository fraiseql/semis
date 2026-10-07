# Deploying semis.fraiseql.dev

The site is static: Astro writes it to `site/dist/`, and nginx serves it on the server
that serves fraiseql.dev. What is served is always a release's site. The Publish
workflow builds it from the `v*` tag, checks that it names that tag's version, and
uploads it as the run's `site` artifact. Nothing deploys it automatically.

The values below are placeholders: `your-server` for the server's host, `deploy` for the
account that copies files to it. The web root is `/var/www/semis.fraiseql.dev`.

## 1. DNS

Add one record in the `fraiseql.dev` zone. The site is served by the server that serves
`fraiseql.dev`, so a CNAME follows it:

```
semis.fraiseql.dev.    3600    IN    CNAME    fraiseql.dev.
```

If the zone's provider does not allow a CNAME there, add `A` and `AAAA` records with the
addresses `fraiseql.dev` has. Check the record before going further:

```bash
dig +short semis.fraiseql.dev
```

## 2. The web root

```bash
ssh deploy@your-server
sudo mkdir -p /var/www/semis.fraiseql.dev
sudo chown deploy:www-data /var/www/semis.fraiseql.dev
sudo chmod 755 /var/www/semis.fraiseql.dev
```

## 3. A certificate

The final server block below names its certificate, so nginx refuses it until the
certificate exists. First serve the web root over HTTP, for the ACME challenge only, in
`/etc/nginx/sites-available/semis.fraiseql.dev`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name semis.fraiseql.dev;

    location /.well-known/acme-challenge/ {
        root /var/www/semis.fraiseql.dev;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/semis.fraiseql.dev /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/semis.fraiseql.dev -d semis.fraiseql.dev
```

`certbot certonly` obtains the certificate without editing the nginx configuration, so
the file below stays as written. certbot's timer renews it through the same webroot:

```bash
sudo certbot renew --dry-run
```

## 4. The server block

Replace `/etc/nginx/sites-available/semis.fraiseql.dev` with:

```nginx
# semis.fraiseql.dev: the fraiseql-semis documentation, a static Astro build.

server {
    listen 80;
    listen [::]:80;
    server_name semis.fraiseql.dev;

    # certbot renews through the webroot; everything else goes to HTTPS.
    location /.well-known/acme-challenge/ {
        root /var/www/semis.fraiseql.dev;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    # As fraiseql.dev's: nginx 1.25.1 and later warn that this form is deprecated, and
    # take `listen 443 ssl;` with `http2 on;` instead; earlier ones know only this form.
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name semis.fraiseql.dev;

    ssl_certificate /etc/letsencrypt/live/semis.fraiseql.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/semis.fraiseql.dev/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    root /var/www/semis.fraiseql.dev;
    index index.html;

    # Set here once: an add_header inside a location would drop every one of these.
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Astro names its assets by their content: a changed file is a new name.
    location /_astro/ {
        expires 1y;
    }

    # Every page is a directory's index.html; a missing page is a 404, not the home page.
    location / {
        try_files $uri $uri/ $uri.html =404;
        expires -1;
    }

    error_page 404 /404.html;

    location ~ /\. {
        deny all;
    }

    gzip on;
    gzip_types text/plain text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1000;
    gzip_comp_level 6;
    gzip_vary on;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## 5. Deploy a release's site

Each `v*` tag's Publish run uploads the site it built as the artifact `site`. Download
the one for the tag, here `v0.1.1`, and copy it to the web root:

```bash
tag=v0.1.1
run=$(gh run list --repo fraiseql/semis --workflow publish.yml --branch "$tag" \
      --status success --limit 1 --json databaseId --jq '.[0].databaseId')
gh run download "$run" --repo fraiseql/semis --name site --dir "site-$tag"
grep -q "semis version ${tag#v}: releases" "site-$tag/index.html"
rsync -avz --delete "site-$tag/" deploy@your-server:/var/www/semis.fraiseql.dev/
```

`--delete` removes the pages a release no longer has, so the server holds exactly the
tag's site. The `grep` is the check the workflow made: the site names the release.

An artifact is kept for 90 days. For an older tag, build the same site from the tag:

```bash
git checkout v0.1.1
cd site
bun install --frozen-lockfile
bun run build
bun run check-links
```

and copy `site/dist/` as above.

## 6. Check it

```bash
curl -sI http://semis.fraiseql.dev/ | head -n 3        # 301 to https
curl -sI https://semis.fraiseql.dev/ | head -n 1       # 200
curl -sI https://semis.fraiseql.dev/missing/ | head -n 1   # 404
curl -s https://semis.fraiseql.dev/ | grep -o 'semis version [^"]*'
```

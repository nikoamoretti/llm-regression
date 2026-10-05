# Results site placeholder

The published results site lives in `site/` on the `results` branch, written by
`python -m runner results push`. The page's source is `runner/site/`.

This directory exists on code branches only so the Vercel project's root
directory (`site`) is present everywhere. Its ignore command then skips every
branch except `results`, instead of failing with "Root Directory does not exist".

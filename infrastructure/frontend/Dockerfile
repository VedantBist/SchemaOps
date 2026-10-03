# CausalOps console: built with Vite, served by nginx, which also proxies /api to the platform API
# (one origin, so the browser needs no CORS and server-sent events stream through).
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm install --no-audit --no-fund && npm run build

FROM nginx:1.27-alpine
COPY infrastructure/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

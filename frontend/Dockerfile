# VitaFlow 前端镜像：Vue/Vite 构建产物由 Nginx 托管，并把 /api 反代到后端。
FROM node:22-alpine AS build

WORKDIR /app

# API 地址在构建时固化。默认同源 /api，由 Nginx 反向代理；不要依赖容器内的 .env。
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

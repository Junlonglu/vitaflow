FROM node:22-slim

WORKDIR /app

# 复制锁定依赖并安装（含 devDeps，tsx 用于运行 TypeScript 源码）
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src ./src

# 骨架默认 faux 模式，无需真实模型/网络；生产可改用 real 并挂载 models.json
ENV PI_PROVIDER=faux
EXPOSE 8300

CMD ["node", "--import", "tsx", "src/server.ts"]

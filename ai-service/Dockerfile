FROM node:22-slim

WORKDIR /app

# 复制锁定依赖并安装（含 devDeps，tsx 用于运行 TypeScript 源码）
COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src ./src

# 骨架默认 faux 模式，无需真实模型/网络；生产可改用 real 并挂载 models.json
ENV PI_PROVIDER=faux

# 非 root 运行（RF-037）：node 镜像自带 uid 1000 的 node 用户。
# .pi-agent 挂的是宿主机目录（models.json），属主对不对取决于部署机；
# 这里把容器内默认目录先交给 node，宿主机挂载后仍以宿主权限为准。
RUN mkdir -p /app/.pi-agent && chown -R node:node /app
USER node

EXPOSE 8300

CMD ["node", "--import", "tsx", "src/server.ts"]

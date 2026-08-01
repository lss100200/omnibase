ARG NODE_VERSION=20
ARG PNPM_VERSION=9.12.3

FROM node:${NODE_VERSION}-alpine AS base
ARG PNPM_VERSION
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
RUN corepack enable \
    && corepack prepare pnpm@${PNPM_VERSION} --activate
WORKDIR /app

FROM base AS dev
ENV NODE_ENV=development
COPY package.json pnpm-lock.yaml .npmrc* ./
RUN pnpm install --frozen-lockfile
COPY . .
EXPOSE 3000
CMD ["pnpm", "dev", "-H", "0.0.0.0", "-p", "3000"]

FROM base AS builder
ARG API_PROXY_URL=http://backend:8000
ENV NEXT_TELEMETRY_DISABLED=1
ENV API_PROXY_URL=$API_PROXY_URL
COPY package.json pnpm-lock.yaml .npmrc* ./
RUN pnpm install --frozen-lockfile --prod=false
COPY . .
RUN mkdir -p public \
    && NODE_ENV=production pnpm build

FROM node:${NODE_VERSION}-alpine AS production
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
WORKDIR /app

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 --ingroup nodejs nextjs

COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]

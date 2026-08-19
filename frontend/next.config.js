/** @type {import('next').NextConfig} */
const nextConfig = {
  // 生成可直接由 Node.js 运行的最小生产镜像产物
  output: 'standalone',

  // 强类型 + React 严格模式
  reactStrictMode: true,

  // API 与健康检查均由 Route Handlers 转发。桌面模式只允许精确的
  // health/readiness 路由，在服务端注入每次启动的授权令牌，并在
  // /health 上完成 challenge-HMAC 证明；浏览器永远接触不到任何启动
  // 身份。Owner / Workspace 读写只走 Electron IPC + backend-only
  // native control token。
  // 不要恢复 rewrites，它既无法完成该身份边界，也会缓冲 SSE 响应。

  // 允许的 image 域名（MinIO presigned URL 的 host）
  // Phase 0 暂留 localhost，Phase 1 接入对象预览时再扩展
  images: {
    remotePatterns: [
      { protocol: 'http', hostname: 'localhost', port: '9000' },
      { protocol: 'http', hostname: '127.0.0.1', port: '9000' },
      { protocol: 'http', hostname: 'minio', port: '9000' },
    ],
  },

  // 生产构建时禁用 x-powered-by header
  poweredByHeader: false,

  // 实验性：typed routes（Next.js 14 仍 beta，开启后提升类型安全）
  // 暂不启用避免与第三方库冲突，稳定后开启
  // experimental: { typedRoutes: true },
}

module.exports = nextConfig

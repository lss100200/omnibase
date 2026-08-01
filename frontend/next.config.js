/** @type {import('next').NextConfig} */
const nextConfig = {
  // 生成可直接由 Node.js 运行的最小生产镜像产物
  output: 'standalone',

  // 强类型 + React 严格模式
  reactStrictMode: true,

  // 开发模式下的 API 代理，避免前端跨域问题
  // 注意：rewrites 在 Next.js 服务端执行（容器内），所以用 docker compose
  // 的服务名 'backend'，不是 'localhost'
  async rewrites() {
    const apiBaseUrl = process.env.API_PROXY_URL || 'http://backend:8000'
    return [
      {
        source: '/api/:path*',
        destination: `${apiBaseUrl}/api/:path*`,
      },
      {
        source: '/health',
        destination: `${apiBaseUrl}/health`,
      },
      {
        source: '/health/ready',
        destination: `${apiBaseUrl}/health/ready`,
      },
    ]
  },

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

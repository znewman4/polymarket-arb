FROM node:20-slim

WORKDIR /app

COPY deploy/signer/package.json ./
RUN npm install --production

COPY deploy/signer/server.js ./

EXPOSE 7777

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "require('http').get('http://localhost:7777/health', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["node", "server.js"]

FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --omit=dev

FROM node:22-alpine

RUN apk --no-cache add ca-certificates

WORKDIR /app

COPY --from=builder /app/node_modules ./node_modules
COPY . .

EXPOSE 8080

CMD ["node", "src/index.js"]

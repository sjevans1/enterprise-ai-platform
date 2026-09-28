# ─────────────────────────────────────────────────────────────────────
# Frontend Dockerfile — Multi-stage build
# Stage 1: Build the React app with Vite
# Stage 2: Serve with nginx
# ─────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /app

# Install dependencies
COPY frontend/package.json ./
RUN npm install -g pnpm && pnpm install

# Copy source
COPY frontend/ ./

# Build
RUN pnpm run build

# ── Production stage ────────────────────────────────────────────────
FROM nginx:1.27-alpine AS production

# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html

# Custom nginx config
COPY docker/frontend/nginx.conf /etc/nginx/nginx.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

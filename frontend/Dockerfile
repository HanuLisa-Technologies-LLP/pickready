# Next.js standalone build. One image, valid in every environment: nothing
# about the backend's address is baked in (see next.config.js rewrites and the
# API_BASE comment in lib/api.ts), so the same digest can be promoted from
# staging to production without a rebuild.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# NODE_ENV=production selects the `.next` distDir (next.config.js switches on
# it), which is where the standalone output below is copied from.
ENV NODE_ENV=production

# Firebase web config. These MUST be present at build time, not deploy time:
# NEXT_PUBLIC_ values are inlined into the bundle by the compiler, and
# lib/firebase.ts calls initializeApp() at module scope, so a missing key both
# fails the prerender pass here and would ship a bundle whose auth can never
# initialise.
#
# Unlike RAZORPAY_KEY_SECRET these are NOT secrets. Firebase web config is
# delivered to every browser that loads the app by design; access is controlled
# by Firebase Auth rules and the authorised-domains list, not by hiding the key.
# Baking them in does pin an image to one Firebase project, so staging and
# production need separate builds.
ARG NEXT_PUBLIC_FIREBASE_API_KEY
ARG NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN
ARG NEXT_PUBLIC_FIREBASE_PROJECT_ID
ARG NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET
ARG NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID
ARG NEXT_PUBLIC_FIREBASE_APP_ID
ENV NEXT_PUBLIC_FIREBASE_API_KEY=$NEXT_PUBLIC_FIREBASE_API_KEY \
    NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=$NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN \
    NEXT_PUBLIC_FIREBASE_PROJECT_ID=$NEXT_PUBLIC_FIREBASE_PROJECT_ID \
    NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=$NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET \
    NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=$NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID \
    NEXT_PUBLIC_FIREBASE_APP_ID=$NEXT_PUBLIC_FIREBASE_APP_ID

# Fail here rather than shipping an image whose login is dead. Without this the
# build succeeds, the container starts, every page renders, and the only symptom
# is that nobody can sign in, which is expensive to diagnose in production.
# The \$ is escaped so the SHELL expands it at run time. An unescaped $ is
# substituted by the builder into the instruction text, which prints the value
# into the build log; harmless for a public Firebase key, but the wrong habit to
# copy the next time this pattern is reached for.
RUN if [ -z "\$NEXT_PUBLIC_FIREBASE_API_KEY" ] || [ -z "\$NEXT_PUBLIC_FIREBASE_PROJECT_ID" ]; then \
      echo "ERROR: Firebase web config missing. Pass --build-arg NEXT_PUBLIC_FIREBASE_API_KEY=... (see docs/DEPLOY_GCP.md)." >&2; \
      exit 1; \
    fi

RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1

# Cloud Run injects PORT (8080) and routes to it. Next's standalone server
# reads PORT, but it also defaults HOSTNAME to localhost, which would accept
# only loopback traffic: the container would pass no health check and every
# revision would fail to go ready. Binding 0.0.0.0 is what makes it reachable.
ENV PORT=8080 HOSTNAME=0.0.0.0

# Run unprivileged. There is no compose file in the cloud to select a user, so
# the image has to pick one itself or it runs as root.
RUN addgroup -g 10001 -S nodejs && adduser -u 10001 -S nextjs -G nodejs

# The standalone bundle carries its own minimal node_modules; static/ and
# public/ are not included in it and must be copied alongside.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 8080
CMD ["node", "server.js"]

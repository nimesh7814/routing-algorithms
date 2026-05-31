FROM nginx:alpine

# Copy static files
COPY index.html /usr/share/nginx/html/index.html
COPY app.js /usr/share/nginx/html/app.js
COPY style.css /usr/share/nginx/html/style.css

# Copy nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Entrypoint: inject BACKEND_URL into index.html at runtime, then start nginx
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/docker-entrypoint.sh"]

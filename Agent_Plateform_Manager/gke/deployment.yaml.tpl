apiVersion: apps/v1
kind: Deployment
metadata:
  name: arm-web
  namespace: arm-stage1
spec:
  replicas: 1
  selector:
    matchLabels:
      app: arm-web
  template:
    metadata:
      labels:
        app: arm-web
    spec:
      containers:
        - name: web
          image: ${APP_IMAGE}
          imagePullPolicy: Always
          env:
            - name: WEB_CONCURRENCY
              value: "2"
            - name: DEFAULT_CPU_MS
              value: "8"
          ports:
            - containerPort: 8080
              name: http
          resources:
            requests:
              cpu: "2"
              memory: 2Gi
            limits:
              cpu: "2"
              memory: 2Gi
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 2
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 10

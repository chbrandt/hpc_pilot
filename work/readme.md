# wstunnel-test

Deployment of wstunnel server and two other containers in the pod: curl and nginx.
This way one can test the wstunnel service by connecting in both directions.

The wstunnel (server) answers on two ports: 
- 8080, exposed as a service (through an Ingress) for wstunnel-client,
- 3000, exposed internally (through a NodePort) for curl and nginx.

### Deployment

The deployment uses namespace `test`. Make sure you are not using this namespace for other purposes.

```bash
kubectl delete namespace test
```

If you are using Minikube (on MacOS), expose services to localhost:

```bash
minikube tunnel
```

Deploy wstunnel-test:

```bash
kubectl apply -f wstunnel-test.yaml
```

Check objects deployed:

```bash
kubectl get pods,deploy,svc,ingress -n test
```

Output looks like this:
```
NAME                                   READY   STATUS    RESTARTS   AGE
pod/wstunnel-server-6595db5b8f-wr7zm   3/3     Running   0          63s

NAME                              READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/wstunnel-server   1/1     1            1           63s

NAME                       TYPE        CLUSTER-IP    EXTERNAL-IP   PORT(S)    AGE
service/wstunnel-service   ClusterIP   10.98.50.38   <none>        8080/TCP   63s

NAME                                         CLASS   HOSTS             ADDRESS        PORTS   AGE
ingress.networking.k8s.io/wstunnel-ingress   nginx   userx.dev.local   192.168.49.2   80      63s
```
 List the containers in the pod:
```
kubectl get -n test pod/wstunnel-server-6595db5b8f-wr7zm -o jsonpath='{.spec.containers[*].name}'
# wstunnel curl nginx
```

Add `userx.dev.local` to your `/etc/hosts` file:
```
127.0.0.1 localhost userx.dev.local
```

### Test wstunnel local-to-remote

Start wstunnel client in your machine (localhost):

```
wstunnel client \
    --http-upgrade-path-prefix 'h3GywpDrP6gJEdZ6xbJbZZVFmvFZDCa4KcRd' \
    -L 'tcp://3000:localhost:80' \
    ws://userx.dev.local:80
```

This will start a local server listening on port 3000, which will forward traffic to the remote server running on (pod's) port 80 (where nginx is running).

You can now access the remote server by visiting `http://localhost:3000` in your browser.
You should see Nginx's default page:

```
Welcome to nginx!
If you see this page, nginx is successfully installed and working. [...]
```

### Test wstunnel remote-to-local

Start wstunnel server in your machine (localhost):

```
wstunnel client \
    --http-upgrade-path-prefix 'h3GywpDrP6gJEdZ6xbJbZZVFmvFZDCa4KcRd' \
    -R 'tcp://3000:localhost:3000' \
    ws://userx.dev.local:80
```

This will forward traffic from the remote to the localhost at port 3000.

Start a local server listening on port 3000, and create a dummy `index.html`:
```
echo 'Hello, World!' > index.html
python -m http.server 3000
```

From the `curl` container in the pod, check access to the local server:
```
kubectl exec -n test pod/wstunnel-server-6595db5b8f-wr7zm -c curl -- curl -s http://localhost:3000/index.html
```

Which should return:
```
Hello, World!
```

This gives you a simple way to test the wstunnel remote-to-local and local-to-remote functionality.

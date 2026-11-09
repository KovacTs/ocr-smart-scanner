# Guía Paso a Paso desde Cero: Despliegue en Oracle Cloud (OCI), Red VCN, Object Storage S3 y Docker

Este documento contiene el tutorial completo para desplegar **Smart OCR Scanner** en **Oracle Cloud Infrastructure (OCI)** partiendo desde una cuenta recién creada sin ningún recurso previo.

---

## FASE 1: Crear la Red Virtual en la Nube (VCN)

1. Inicia sesión en [cloud.oracle.com](https://cloud.oracle.com).
2. Asegúrate de estar en tu **Región Principal** (ej. *Chile Central - Santiago (`sa-santiago-1`)*, *US East - Ashburn*, etc.).
3. Abre el menú lateral (**☰**) en la esquina superior izquierda.
4. Ve a **Networking** ➔ **Virtual Cloud Networks**.
5. Haz clic en el botón azul **Start VCN Wizard** (Iniciar asistente de VCN).
6. Selecciona **Create VCN with Internet Connectivity** y haz clic en **Start VCN Wizard**.
7. Configura:
   - **VCN Name:** `ocr-scanner-vcn`
   - **Compartment:** El predeterminado (raíz).
   - **VCN CIDR Block:** `10.0.0.0/16`
   - **Public Subnet CIDR Block:** `10.0.0.0/24`
   - **Private Subnet CIDR Block:** `10.0.1.0/24`
8. Haz clic en **Next** y luego en **Create**.
9. Una vez completado, haz clic en **View VCN**.

---

## FASE 2: Abrir Puertos en la Security List (Firewall de OCI)

1. En la página de tu VCN (`ocr-scanner-vcn`), en el menú lateral izquierdo (*Resources*), haz clic en **Security Lists**.
2. Haz clic en **Default Security List for ocr-scanner-vcn**.
3. En **Ingress Rules**, haz clic en **Add Ingress Rules**.
4. Agrega las reglas para permitir acceso a la web y a la API:

| Ingress Rule | Source CIDR | IP Protocol | Destination Port Range | Descripción |
| :--- | :--- | :--- | :--- | :--- |
| **HTTP** | `0.0.0.0/0` | TCP | `80` | Proxy Caddy HTTP |
| **HTTPS** | `0.0.0.0/0` | TCP | `443` | Proxy Caddy SSL/TLS |
| **FastAPI** | `0.0.0.0/0` | TCP | `8000` | Acceso directo API / Swagger |

5. Haz clic en **Add Ingress Rules**.

---

## FASE 3: Crear Bucket en OCI Object Storage y Credenciales S3

### 3.1 Crear el Bucket
1. Menú **☰** ➔ **Storage** ➔ **Object Storage & Archive Storage** ➔ **Buckets**.
2. Haz clic en **Create Bucket**:
   - **Bucket Name:** `ocr-scanner-documents`
   - **Default Storage Tier:** **Standard**
   - **Encryption:** *Encrypt using Oracle-managed keys*
3. Haz clic en **Create**.

### 3.2 Obtener el Namespace
En la misma vista del bucket, copia el valor de **Object Storage Namespace** (cadena alfanumérica única de tu tenancy, ej. `ax9b3c4def10`).

### 3.3 Generar Credenciales S3 (Customer Secret Keys)
1. Haz clic en el icono de tu perfil de usuario (arriba a la derecha) ➔ **User settings**.
2. En el menú lateral izquierdo (*Resources*), haz clic en **Customer Secret Keys**.
3. Haz clic en **Generate Secret Key**:
   - **Name:** `ocr-app-storage-key`
   - Haz clic en **Generate Secret Key**.
4. ⚠️ **Copia y guarda tu Secret Key** inmediatamente (no se volverá a mostrar).
5. En la fila recién creada, copia el valor de la columna **Access Key**.

**Resumen de valores para el `.env`:**
- **Endpoint S3:** `https://<TU_NAMESPACE>.compat.objectstorage.<TU_REGION>.oraclecloud.com`
- **Region:** Ej. `sa-santiago-1`
- **Bucket:** `ocr-scanner-documents`
- **Access Key:** Copiada de la tabla.
- **Secret Key:** Copiada en el paso 4.

---

## FASE 4: Crear la Máquina Virtual (Compute Instance)

1. Menú **☰** ➔ **Compute** ➔ **Instances**.
2. Haz clic en **Create Instance**:
   - **Name:** `ocr-server-vm`
   - **Image:** Haz clic en *Edit* ➔ Selecciona **Canonical Ubuntu** (`22.04` o `24.04`).
   - **Shape:** 
     - *Recomendado (Always Free ARM):* **Ampere VM.Standard.A1.Flex** (2 a 4 OCPUs, 12 a 24 GB RAM).
     - *Alternativa (Always Free AMD):* **VM.Standard.E2.1.Micro** (1 OCPU, 1 GB RAM).
   - **Networking:**
     - Virtual cloud network: `ocr-scanner-vcn`
     - Subnet: `Public Subnet-ocr-scanner-vcn`
     - Public IPv4: **Assign a public IPv4 address** (Marcar Sí).
   - **SSH Keys:** Selecciona *Generate SSH key pair* y haz clic en **Save private key** (descargará tu archivo `.key`).
3. Haz clic en **Create**.
4. En 1-2 minutos el estado pasará a **RUNNING**. Copia la **Public IP Address** (ej. `140.238.180.45`).

---

## FASE 5: Conexión SSH y Desbloqueo del Firewall del SO (iptables)

En Ubuntu sobre OCI, `iptables` bloquea por defecto los puertos entrantes aunque estén habilitados en la consola.

```bash
# 1. Conectar por SSH
chmod 400 ~/Descargas/ssh-key-ocr.key
ssh -i ~/Descargas/ssh-key-ocr.key ubuntu@<TU_IP_PUBLICA>

# 2. Desbloquear puertos 80, 443 y 8000 en iptables
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8000 -j ACCEPT

# 3. Guardar las reglas para que persistan tras reiniciar
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
sudo netfilter-persistent save

# 4. (Opcional - solo si usas VM Micro de 1 GB RAM) Crear 3 GB de SWAP
sudo fallocate -l 3G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## FASE 6: Instalar Docker y Desplegar el Backend

### 6.1 Instalar Docker
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker ubuntu
newgrp docker
```

### 6.2 Clonar y Configurar
```bash
git clone https://github.com/tu-usuario/ocr-smart-scanner.git
cd ocr-smart-scanner

cp .env.example .env
nano .env
```

Configura en el `.env`:
```bash
PROJECT_NAME="Smart OCR Scanner API"
SECRET_KEY=clave-secreta-jwt-super-segura-en-produccion-2026
DOMAIN=:80

STORAGE_BACKEND=S3
S3_ENDPOINT_URL=https://<TU_NAMESPACE>.compat.objectstorage.<TU_REGION>.oraclecloud.com
S3_REGION_NAME=sa-santiago-1
S3_BUCKET_NAME=ocr-scanner-documents
S3_ACCESS_KEY_ID=<TU_CUSTOMER_ACCESS_KEY>
S3_SECRET_ACCESS_KEY=<TU_CUSTOMER_SECRET_KEY>

LLM_PROVIDER=AUTO
```

### 6.3 Levantar con Docker Compose
```bash
docker compose up --build -d
```

### 6.4 Validar
```bash
docker compose ps
curl http://localhost:8000/health
```

---

## FASE 7: Pruebas y Demo

1. **Swagger UI:** Abre en tu navegador `http://<TU_IP_PUBLICA>:8000/docs`.
2. **Usuario Demo:**
   - Email: `auditor@empresa.cl`
   - Password: `PasswordSeguro123!`
3. **App Móvil (Expo):**
   En tu entorno local edita `mobile/.env`:
   ```bash
   EXPO_PUBLIC_API_URL=http://<TU_IP_PUBLICA>:8000/api
   ```
   Inicia la app con `cd mobile && npx expo start` y escanea el código QR con Expo Go. Las fotos se sincronizarán directamente con tu bucket de Oracle Cloud.

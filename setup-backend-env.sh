#!/bin/bash

# Backend Environment Variables Setup Script
# Generated from Vly for Git Sync
# Run this script to set up your Convex backend environment variables

echo 'Setting up Convex backend environment variables...'

# Check if Convex CLI is installed
if ! command -v npx &> /dev/null; then
    echo 'Error: npx is not installed. Please install Node.js and npm first.'
    exit 1
fi

echo "Setting JWKS..."
bunx convex env set "JWKS" -- "{\"keys\":[{\"kty\":\"RSA\",\"n\":\"yDKRaFVgREJS3jwkGc_qIAqnXDxSk5xJ8kYK39BYcdRtDj8p3pRR6XCTUwfwbAqiEeI8itQmJMbOdV0WfrDXrMjS-xZaZ6xK_OPAAOAFITH4Y23BguEYo4f3iG9-WM4sOsZwIPB_QWUgcjCBTzIFRmOBKRy6udoVJ9PrUQmP8M_p8kfTxfoCppRnpdufFFswsq7W4wLpthVZGqlqNUzxnd1yJm4DT6p1SerlGd-CnGLWSxSQk5dqiJvISybNKtOU39HhbGTX8VX02_lwMfdosSTvE7Fr_xg_71YkQ6XcP66p_4HJhL2RDzmqAwZIaIgf37OfV6eX0r2TPb5AWcRdhw\",\"e\":\"AQAB\",\"use\":\"sig\"}]}"

echo "Setting JWT_PRIVATE_KEY..."
bunx convex env set "JWT_PRIVATE_KEY" -- "-----BEGIN PRIVATE KEY----- MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDIMpFoVWBEQlLe PCQZz+ogCqdcPFKTnEnyRgrf0Fhx1G0OPynelFHpcJNTB/BsCqIR4jyK1CYkxs51 XRZ+sNesyNL7FlpnrEr848AA4AUhMfhjbcGC4Rijh/eIb35Yziw6xnAg8H9BZSBy MIFPMgVGY4EpHLq52hUn0+tRCY/wz+nyR9PF+gKmlGel258UWzCyrtbjAum2FVka qWo1TPGd3XImbgNPqnVJ6uUZ34KcYtZLFJCTl2qIm8hLJs0q05Tf0eFsZNfxVfTb +XAx92ixJO8TsWv/GD/vViRDpdw/rqn/gcmEvZEPOaoDBkhoiB/fs59Xp5fSvZM9 vkBZxF2HAgMBAAECggEASUVItrjYnOLxVWkJhXD1LXj4u+dQCbpfzg4YmMW2OSMY LuirOE1slVWgdfkn40MZAhadNepjc35XvdiuDPxIU3LE1STfPaZvY5MviKJ0/is9 z5YyBua/j8qJrCOySGpdAb3y9/tNd++9Kk3aZdPW3mY4tYVQSgkpSQRk0aoARpAM NpooBfWLEv+5fIA/HQ6Sub4kfPLQ/sXLHJxvsTroH60KYx0A2VO1Tj+4NrltdW2a WWS+9wyLAThQjcS7BGW4cCQqUaKs4IkyhRkMKvSlGFLrzXOtovKMR/8luHYuwugm 1N0eor6G9whx7oZZo5Aqgw7uCS9fDhcp5tDYiSRIEQKBgQDoxtlo7Pc3V+8iNqeg YaFqBqqjYUogWI0S7CbHWp+Z37c3TNq2G/Z7vyLiwrFDXgbWTcq2Sj6yY8LqrbvK TxiU+3tSJQzkmrbR0D/mAPpALUGlW9ok97P5a/bTj+A+LEjwY1natqcojqQNKzxc 39dJFy1hnG/jOTmMU0brSYDAKwKBgQDcK6QNeuFw5ur7O+l7G9tl4wPmX20zpoFR EE+S5CB9ImXnV/LuW+8TEncq73yViRRjIUQK9QO9vTMC57V/49AJoImXsOjBP4vJ ThJz7XwfWPYXnZdV0JhYllVfAhG+rjVmpkDI4AFtrDM9scTWh9bKfxRZhbXFZ5Bz SeUpy7bOFQKBgGP+r/xz90sN+ksvQVkTN5ztWjYvIAG/KHMdgRcYZgFa6kDWQgoC /yJvnFPfIPj4lmVPK6qdQEuvsVbQ5R/mVIADfBlwkxZNJAYDuL9cYiRZjJ61U4OX 6PdXmsONRd5PddHWTA45cptoky6ZCSg4fCoIy9Tnt+Tbe049o/SFMcrbAoGAMYI6 vLFCGpJCisYZJY7JEisvSFFzC+dIMwNY8W4NBDaE9bWoMgNISmCfnW8G89VEWVo0 o8Ye1j7CRsf131FKCbAo4Ixuem4gr963dYwUmjP1+q98RVbHuzvq7JdZiChCZ9fA v6rnh1LuntPnsFoRaa2T1OGlU0OLjvEx7+kYLVUCgYEAz5VEkUY3zOs2H923FyN4 zz63DjFAkARgrBh1SMAzKLxvhtkNGGqssSECtFKwK9A2dXOp3QPvKelXIRyyCx/N jDsPHqM9nOHZdfF7Xt8hr7d1vJ9ByEUH4srI+1xMAPk3q81W4vS+XMPWMgfEZIM/ R0D9J2QS/kSZO8s4J0wdURA= -----END PRIVATE KEY-----"

echo "Setting SITE_URL..."
bunx convex env set "SITE_URL" -- "https://cheerful-grasshopper-726.convex.site"

echo "Setting VLY_APP_NAME..."
bunx convex env set "VLY_APP_NAME" -- "Alpine Trade View"

echo "Setting VLY_CONVEX_AUTH_ISSUER..."
bunx convex env set "VLY_CONVEX_AUTH_ISSUER" -- "https://freebuff.com"

echo "Setting VLY_INTEGRATION_BASE_URL..."
bunx convex env set "VLY_INTEGRATION_BASE_URL" -- "https://integrations.vly.ai/"

echo "Setting VLY_INTEGRATION_KEY..."
bunx convex env set "VLY_INTEGRATION_KEY" -- "sk_d4d67a4748fc9e6cdf7d045fb564a4f110d91408bc05ff14a60a633438b12d5b"

echo "✅ All backend environment variables have been set!"
echo "You can now run: pnpm dev:backend"

# GTA Clipadas

Plataforma de cortes de GTA 6: upload (ou link) → trecho → 36 visuais → 36 legendas → corte 9:16 com legenda automática e capa.
Contas com confirmação por SMS, planos com cota mensal, pagamento pelo Mercado Pago, corte em lote e fila prioritária no Pro, painel de admin.

## Rodar local
    pip install -r requirements.txt     # precisa de ffmpeg
    DEV_SHOW_CODES=1 ADMIN_EMAILS=voce@exemplo.com uvicorn app.main:app --port 8080
Sem as chaves, SMS e e-mail ficam em modo teste (o código aparece no log e, com DEV_SHOW_CODES=1, na tela).

## Colocar no ar (Railway)
1. Suba esta pasta num repositório do GitHub e crie um projeto no Railway a partir dele (usa o Dockerfile e o railway.json).
2. **Volume**: crie um Volume montado em `/data`. Sem ele, contas e vídeos somem a cada deploy.
3. **Variables**: preencha as do `.env.example`.
4. **Domínio**: Settings → Networking → Custom Domain (ou Generate Domain). Coloque o endereço em `PUBLIC_URL`.
5. **Asaas** (padrão se `ASAAS_API_KEY` existir): Integrações → Webhooks → Criar webhook com a URL `https://SEU-DOMINIO/api/pay/asaas`, eventos *Cobrança recebida* e *Cobrança confirmada*, e um token de 32+ caracteres; o mesmo token vai em `ASAAS_WEBHOOK_TOKEN`.
   **Mercado Pago** (alternativa): em Suas integrações → Webhooks, cadastre `https://SEU-DOMINIO/api/pay/webhook` com o evento *Pagamentos* e copie a assinatura secreta para `MP_WEBHOOK_SECRET`.
6. Entre no site com o e-mail de `ADMIN_EMAILS` e abra `/admin`.

## Planos (app/plans.py)
| Plano | Preço | Cortes | Prazo | Libera |
|---|---|---|---|---|
| Grátis | R$ 0 | 3 (uma vez, por celular) | — | 9 visuais, 4 legendas, logo no vídeo |
| Iniciante | R$ 19,90 | 30 | 30 dias | 18 visuais, 12 legendas, sem logo |
| Clipador | R$ 39,90 | 150 | 30 dias | tudo, sem logo |
| Pro | R$ 79,90 | 300 | 30 dias | tudo + lote + fila prioritária + 3 GB |
| Passe GTA 6 | R$ 9,90 | 30 | 7 dias | tudo, sem logo |

Cada compra libera o prazo do plano (sem renovação automática). Se o corte falhar, o crédito volta.
Os cortes ficam 30 dias (`RETENTION_DAYS`) e o vídeo enviado é apagado logo após o corte.

## Onde mexer
- `app/plans.py` preços e cotas · `app/templates.py` visuais/legendas e em que plano entram
- `app/accounts.py` contas, SMS, cotas · `app/payments.py` Mercado Pago · `app/notify.py` SMS/e-mail
- `app/static/termos.html` e `privacidade.html`: **preencha razão social, CNPJ e contato e peça revisão de um advogado**
- Lista de espera do plano automático: `/data/lista_espera_automatico.jsonl` (também no /admin)

# Fase 2 — Registro de performance por publish_id

Data: 2026-07-19. Status: rascunho de design, decisões tomadas de forma autônoma a pedido do
usuário ("pode fazer todos em paralelo"). Aguardando revisão antes de virar plano de
implementação.

## Contexto

A visão maior (combinada no início da sessão) é um loop de feedback: gerar → publicar →
medir → recalibrar o funil. Esta Fase 2 cobre só a ponta de **captura** — registrar, por
`publish_id`, os números de aceitação que o usuário observa manualmente depois de publicar
(views, likes, etc.). **Fase 3** (usar esses números para ajustar o funil — ex.: promover
temas vencedores a exemplo-ouro do escritor) fica explicitamente fora de escopo aqui.

## Decisão de design: onde os dados moram

**Um arquivo único, cross-run, append-only**: `data/media-pipeline/performance-log.json`
(gitignored, como todo `data/media-pipeline/`). Não um arquivo por run — o usuário só vai ter
números de engajamento dias ou semanas depois do run acontecer, quando o diretório do run já
pode ter sido limpo ou movido. Um log central sobrevive a isso.

Formato: lista de registros, um por medição (permite múltiplas medições ao longo do tempo
para o mesmo vídeo — ex. "24h depois" e "7 dias depois"):

```json
[
  {
    "publish_id": "phase1-02",
    "platform": "youtube_shorts",
    "captured_at": "2026-07-26T10:00:00-03:00",
    "metrics": {"views": 1200, "likes": 84, "comments": 3, "shares": 5}
  }
]
```

`metrics` é um dict livre (não um schema fixo) porque YouTube/TikTok/Reels não compartilham
nomenclatura de métricas — força um schema comum agora seria prematuro (YAGNI). `platform` e
`captured_at` são os únicos campos obrigatórios além de `publish_id` e `metrics`.

## Componente: script `scripts/record_performance.py`

Segue o padrão do repo de um script por finalidade (`reddit_popular_curation.py`,
`render_audit_report.py`). Uso:

```bash
python3 scripts/record_performance.py --publish-id phase1-02 --platform youtube_shorts \
  --metric views=1200 --metric likes=84 --metric comments=3
```

- Append-only: nunca edita ou remove uma medição anterior do mesmo `publish_id` — cada
  chamada gera uma nova entrada com seu próprio `captured_at` (hora da chamada).
- `--metric key=value` repetível; valores parseados como número quando possível, senão string.
- Sem validação de que `publish_id` existe em algum `concepts.json` — o log é intencionalmente
  desacoplado de runs específicos (ver decisão acima); cruzar `publish_id` com o conceito de
  origem é problema da Fase 3, não desta fase.

## Fora de escopo (explícito)

- Qualquer uso desses números para alterar o funil (Fase 3).
- Coleta automática via API das plataformas (o usuário fornece manualmente — decisão já
  tomada no início da sessão, junto com "publicação manual, sem API de upload").
- Join automático entre `performance-log.json` e o `concepts.json` de origem.

## Testes

Padrão unittest existente:
1. Script sem log prévio cria o arquivo com a primeira entrada.
2. Chamada subsequente para o mesmo `publish_id` acrescenta (não sobrescreve) uma nova entrada.
3. `--metric` parseia número quando possível (`"1200"` → `1200`), mantém string quando não
   (`"n/a"` → `"n/a"`).
4. Múltiplos `--metric` viram múltiplas chaves em `metrics`.

# 2026-07-08 — E2E com posts frescos: calibração do funil

Primeira rodada ponta-a-ponta com o grafo oficial (05) após a aprovação do Gerald em
5 s / 8 s / 10,3 s (2 segmentos). Objetivo: validar o funil inteiro com matéria-prima real.

## Rodadas

| Rodada | Fonte | Gate de fonte | Gate de humor | Observação |
|---|---|---|---|---|
| r/popular, limit 10 | live | 0/10 | — | lote dominado por tweets, scoreboards, documentos — rejeições legítimas por dependência de texto |
| subreddits visuais (cats, aww, AnimalsBeingDerps, mildlyinteresting, funny), limit 15 | live | 6/15 | 0/6 | críticos válidos e com consenso; candidatos do escritor fracos |
| replay congelado + `--concept-timeout 600` | frozen | 7/15 | 0/7 | timeout não era a causa das rejeições (só das falhas "did not return candidates") |
| replay congelado + escritor `qwen3:14b` | frozen | 6/15* | 0/6 | PT-BR mais coerente; laugh 5–7/10 vs corte de 8/10 nos dois críticos |
| replay congelado + `qwen3:14b` + prompt few-shot | frozen | 7/15* | **1/7 aprovado com consenso** | primeiro conceito aprovado pelo escritor autônomo |

\* o gate de fonte usa modelo de visão não-determinístico; a contagem varia ±1 entre replays.

## Primeiro conceito aprovado pelo escritor autônomo

Post "2 years difference" (gato branco dormindo em cama branca):
setup "EU ABRI O BRASIL", escalada "ESPERAVA UMA CENA DE TENSÃO", punchline
"ENCONTREI UM GATO DORMINDO" — consenso dos dois críticos (fit 9, ptbr 8, surprise 9,
laugh 8, visual 7). A mudança que destravou: adicionar ao prompt do escritor o exemplo-ouro
real do Gerald com a regra generalizada ("a virada dá ao sujeito uma profissão, papel social
ou intenção inesperada; esse é o padrão que mais aprova") e a proibição de punchline
desconectada do setup. Default de `--concept-timeout` subiu de 60 s para 600 s.

## Achados

1. **`--only-index` filtra também a geração de conceitos**, não só o render — não usar
   para "limitar renders" em rodada e2e, ou o funil processa um único post.
2. **Gate de fonte saudável**: scores por eixo coerentes; r/popular rende quase zero
   matéria-prima visual; subreddits de animais/fotos rendem ~40-47%.
3. **Críticos saudáveis**: llama3:latest (critic_1) e gemma3:12b (critic_2) devolvem JSON
   no schema e rejeitam com consenso e por mérito.
4. **Gargalo confirmado: o escritor.** qwen3:8b produz punchlines incoerentes em PT-BR
   ("FLORESTA NUNCA FEZ NENHUMA PIZZA", "AÇO E BACON EM FORMA DE CHIFRE") ou paráfrase do
   título. Nota histórica: o Gerald aprovado veio de **seeds curados** (`--concepts-file`),
   não do escritor autônomo — não há registro de conceito aprovado escrito pelo qwen3:8b.
5. **`--concept-timeout` default (60 s) é curto para modelos pensantes** — causa
   "humor writer did not return candidates" com qwen3. A run calibrada do Gerald usava 600 s.
6. O caminho batch de conceitos falha consistentemente ("Ollama did not return a JSON
   array") e o fallback per-post segura — funciona, mas custa uma chamada perdida por rodada.

## Revisão humana do primeiro aprovado e endurecimento do funil

O usuário avaliou o vídeo do conceito aprovado: "imagem, video e audio estão ok... o
conjunto todo não tem um punch comico". Diagnóstico estrutural: o setup era a parte
engraçada e a punchline apenas descrevia a imagem ("ENCONTREI UM GATO DORMINDO") —
o inverso do padrão Gerald. Os críticos não penalizavam punchline descritiva.

Mudanças (cobertas por teste):

1. **Regra determinística** em `humor_candidate_issues`: punchline com ≥60% dos tokens
   presentes na fonte/descrição visual = "punchline apenas descreve a cena visivel" → rejeita.
2. **Rubrica dos críticos**: teste obrigatório da punchline antes de pontuar — punchline
   descritiva ou que desfaz a expectativa sem reinterpretação nova limita laugh/surprise a 5.
3. **Prompt do escritor**: proibição explícita de punchline que revela/descreve a cena;
   o clímax precisa estar na punchline.

## Decisões pendentes

- Subir o default de `--concept-timeout` para 600? (valor comprovado)
- Escritor: qwen3:14b em avaliação; próximos candidatos gemma4:31b (com offload) ou
  melhorar o prompt do escritor (exemplos few-shot de piadas aprovadas, ex.: Gerald).
- Zero-yield em dias de matéria-prima ruim é comportamento correto do design adversarial.

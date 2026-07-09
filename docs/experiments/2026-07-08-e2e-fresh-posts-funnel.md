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

## 2026-07-09 — rodada com o funil endurecido: 0/15, mas causa raiz era outra

Reexecutei os mesmos 15 posts congelados com o funil endurecido (regra determinística +
rubrica). Resultado: 7/15 passaram no gate de fonte, **0/7 no gate de humor** — todos por
"criticos sem consenso ou abaixo dos minimos".

Extraí o candidato mais forte de cada post source-aprovado e mostrei ao usuário 3 deles
lado a lado com a imagem-fonte real (não só o texto). Veredito: **"as 3 são muito boas"** —
incluindo candidatas que os críticos automáticos pontuaram 6-7 (abaixo do corte de 8):

- "EMPREGADO DO MÊS ESTÁ DESATIVADO / O GATO ESTÁ DESCANSANDO NA PRATELEIRA / FALTA DE
  CURIOSIDADE NÃO É FALTA DE FUNCIONÁRIO" (critic_1 laugh=7 approved=true; critic_2 laugh=6
  approved=false)
- "CAVALO E GATO EM ESTALAGEM / ... / UMA TROCA DE INSTRUTOR DE EQUITAÇÃO"
- "CÃO SEGURA PATO COM CHIFRES / ... / NÃO ERA PRA TER PESCOÇO DE PINTA"

**Causa raiz real**: os críticos (`llama3:latest`, `gemma3:12b`) nunca recebem a imagem —
só o `visual_description` textual gerado uma vez pelo modelo de visão no início do funil
(`source = {...}` em `improve_humor_concept`). Julgando humor e "visual_payoff" sem ver a
cena, eles não percebem a nuance que faz a piada funcionar e travam em 6-7. Isso não é um
threshold mal calibrado — é um gate estruturalmente cego à imagem.

### Correção: crítico com visão real

Em vez de só baixar o número de corte (que manteria os críticos adivinhando), demos visão
real a um dos críticos:

- `is_vision_capable_model()` / `encode_image_for_vision()`: helpers novos.
- `improve_humor_concept()` ganha `image_path`; quando o nome do modelo do crítico contém
  `vl`/`vision`/`llava`, a imagem real (JPEG redimensionado, base64) é anexada à mensagem
  via `images: [...]` (mesmo padrão do `describe_source_image`).
- `generate_concepts()` repassa `source_media_paths` (já existente no funil) até o crítico.
- Default de `--humor-second-critic-model`: `gemma3:12b` → `qwen2.5vl:7b` (já instalado).
- 2 testes novos: crítico de visão recebe `images`, crítico de texto não recebe, e sem
  `image_path` nenhum crítico recebe.

### Validação: crítico de visão funciona e destrava aprovação real

Suíte de testes: 28 passed (2 novos). Smoke test isolado de `qwen2.5vl:7b` com `format`
schema + imagem real: responde JSON válido e o raciocínio referencia a cena
("gato descansando na prateleira simboliza uma pessoa desativada no trabalho").

Teste controlado com um post só (sem render, `--no-render`) no post "employee of the month",
mesmo que rejeitou 3x no dia anterior: **aprovado com consenso na 1ª rodada.**

setup "O MELHOR FUNCIONÁRIO", escalada "ESTÁ DORMINDO EM UM BALCÃO DE PAPELARIA",
punchline "E NÃO É UM HUMANO" — candidata 2 do escritor (`qwen3:14b`).

| Crítico | source_fit | natural_ptbr | surprise | laugh | visual_payoff |
|---|---|---|---|---|---|
| llama3:latest (texto) | 8 | 8 | 9 | 8 | 7 |
| qwen2.5vl:7b (visão, imagem real) | 9 | 8 | 9 | **10** | **9** |

O crítico de visão pontuou MAIS alto que o de texto depois de ver a imagem de verdade —
confirma a hipótese: críticos cegos subestimavam sistematicamente. A candidata concorrente
(id 1, "MAS O CAIXA É VAGUARDO" — descritiva) foi corretamente rejeitada por ambos, incluindo
o motivo explícito "a punchline apenas descreve o que a imagem já mostra" — o guard-rail
determinístico/rubrica continua funcionando junto com a visão real.

`execution.state = approved`, `quality reason: limiares obrigatorios atendidos` — primeiro
conceito 100% aprovado pelo funil desde o endurecimento de ontem.

Próximo passo: rodar o funil completo nos mesmos 15 posts congelados com o crítico de visão
para medir o rendimento agregado (dia anterior: 0/15).

## Decisões pendentes

- Validar `qwen2.5vl:7b` como crítico de visão (JSON estruturado + qualidade do julgamento).
- Subir o default de `--concept-timeout` para 600? (valor comprovado) — feito em e242c79.
- Escritor: qwen3:14b + few-shot já melhora; acompanhar taxa de aprovação com o crítico de
  visão antes de decidir se ainda precisa de modelo maior ou outro prompt.
- Zero-yield em dias de matéria-prima ruim é comportamento correto do design adversarial;
  zero-yield por crítico cego à imagem não era — é o que esta correção resolve.

# Roadmap — Media Meme Pipeline

Documento vivo. Atualizado a cada avanço relevante. Não substitui `docs/experiments/*.md`
(logs técnicos detalhados de cada experimento) nem `docs/architecture.md` (arquitetura
estável) — este arquivo é a visão de conjunto: o que foi decidido, por que mudou de rumo,
e onde o projeto está agora.

## Objetivo geral

Pipeline local (Reddit RSS → vídeo-meme) com foco em qualidade real: só renderizar vídeo
quando houver boa evidência de que a piada é específica, legível, ligada ao post e
compartilhável. Render é caro (GPU, minutos por tentativa); a aposta do design é gastar
esse custo só depois que texto e imagem-fonte passarem por um funil adversarial.

Funil em 5 estágios: (1) seleção de posts → (2) gate de adequação da fonte (a imagem
sustenta uma piada sem inventar elementos?) → (3) geração de humor por um escritor e crítica
por dois modelos independentes → (4) imagem-base limpa + roteiro de vídeo → (5) render LTX
2.3 nativo I2V com áudio + revisão humana.

## Estado atual (2026-07-11)

| Estágio | Situação |
|---|---|
| 1. Seleção de posts | Estável. RSS do Reddit, dry-run disponível. |
| 2. Gate de fonte | Estável. Scores coerentes; r/popular rende pouca matéria-prima visual (dependência de texto); subreddits de fotos/animais rendem ~40-47% de aprovação. |
| 3. Humor (escritor + críticos) | **Em calibração ativa.** Taxa de aprovação orgânica: 0/15 → 0/15 → 1/15 → 2/15 nos últimos 4 replays dos mesmos posts congelados, subindo a cada correção (crítico de visão, fix de token budget). Tendência positiva; ver "Próximos passos". |
| 4. Imagem-base + roteiro | Estável, reaproveitado das runs anteriores sem retrabalho. |
| 5. Render de vídeo (LTX 2.3) | **Resolvido tecnicamente.** Grafo oficial (`workflows/05`) validado; aprovado pelo usuário em 5 s, 8 s e 10,3 s (2 segmentos). |
| Render em posts frescos (fora do Gerald) | Ainda não feito — só o texto do conceito foi aprovado numa run e2e até agora; falta gerar imagem-base + vídeo para um post 100% autônomo. |

Branch: tudo commitado direto em `main`. Commits-chave (mais recente primeiro):
`b016ddf` (fix token budget do escritor) → `7ef8ce2` (crítico com visão real) →
`e242c79` (funil endurecido contra punchline descritiva) → `a7dde9e` (render em 2
segmentos) → `4b0b09b` (adoção do grafo oficial LTX 2.3 I2V).

## O que avançamos, em ordem

1. **Contrato de `concepts.json` e crítica adversarial.** Versionamento do schema, dois
   críticos independentes por candidata, crítica ausente/inválida nunca aprova, retomada de
   conceito aprovado via `--approved-concepts-file`.
2. **Conceito Gerald aprovado via seeds curados** (`--concepts-file`) — primeiro sucesso
   ponta-a-ponta do funil (imagem-base + vídeo), mas com curadoria humana no lugar do
   escritor autônomo.
3. **Causa raiz do pseudo-texto/drift no I2V encontrada e corrigida.** O grafo hand-built
   (`04`) rodava um regime de guidance inválido (CFG 3.0/7.0 + STG com LoRA distilled, ¼ da
   resolução de referência, sem passe de refine) — prompt tuning nunca teria corrigido isso.
   Substituído pelo grafo oficial do template ComfyUI (`05`): CFG 1.0, sigmas manuais
   distilled, base em meia resolução + upscale latente ×2 + refine de 3 steps.
4. **Smoke test visual barato** (`scripts/ltx23_visual_smoke_test.py`) para inspecionar
   pseudo-texto/drift sem gastar um render completo.
5. **Vídeo do Gerald aprovado pelo usuário** em três durações (5 s, 8 s, 10,3 s), a última via
   renderização em 2 segmentos com continuação (`--ltx23-segments 2`).
6. **Primeira rodada e2e com posts frescos do Reddit** (não-Gerald): 0/10 no r/popular
   (matéria-prima ruim — tweets, scoreboards, documentos), 0/15 em subreddits mais visuais
   (escritor autônomo fraco).
7. **Feedback do usuário sobre o primeiro vídeo 100% autônomo**: tecnicamente ok, mas "não
   tem punch cômico" — a punchline só descrevia a cena em vez de reinterpretá-la.
8. **Funil endurecido**: regra determinística de overlap de tokens (punchline muito parecida
   com a fonte é rejeitada antes do crítico), rubrica dos críticos mais rígida, prompt do
   escritor com o exemplo-ouro real do Gerald.
9. **Causa raiz real do gate de humor severo demais encontrada com ajuda do usuário**: os
   críticos de texto nunca recebiam a imagem, só uma descrição textual gerada uma vez no
   início do funil — avaliação estruturalmente cega à nuance visual que faz a piada
   funcionar. Corrigido dando visão real a um dos críticos (`qwen2.5vl:7b` recebe a imagem
   via base64).
10. **Fix de um bug lateral**: o escritor truncava a saída JSON sob orçamento de tokens
    marginal (`num_predict=750`) em rodadas de autocorreção mais verbosas, abortando posts
    que já tinham boas candidatas. Corrigido (750 → 1500).
11. **Primeira aprovação orgânica em lote**: 1/15 nos mesmos posts congelados, com o crítico
    de visão e o fix de token budget juntos.
12. **Timeout rígido de 1h** adotado no processo de invocação de runs longas (`timeout 3600
    <comando>` numa única chamada em background, sem waiter secundário), depois de 3
    incidentes em que um processo de aviso (waiter) ficou órfão entre reinícios de sessão e
    nunca notificou — fazendo o trabalho parecer travado por >24h quando na verdade tinha
    terminado em ~20 minutos. Funcionou de primeira: notificação chegou corretamente.
13. **Replay com o timeout wrapper: 2/15 aprovados** — confirma a tendência de melhora e
    valida o padrão de invocação novo. Candidatas: "TREINADOR MANDOU FOTO DE AMIZADE / FOTO
    MOSTRA CAVALO E GATO JUNTOS / TREINAMENTO PARA COEXISTÊNCIA" (scores 8-9) e "EU ABRI O
    BRASIL / O GATO FEZ UMA GALÁXIA DE SONO / EU SOU O ASTRÔNOMO DE MIM" (scores 8-9, mas
    com abertura genérica reciclada — revisar antes de render).

## Descobertas empíricas que forçaram adaptar o plano

Estas não eram previsíveis a partir do design original — só apareceram testando de verdade,
e cada uma mudou o próximo passo:

- **Regime de guidance do LTX 2.3 distilled não é tolerante a mistura.** CFG>1 + STG com o
  LoRA distilled produz pseudo-texto e drift, não importa o prompt. A correção certa era
  trocar o grafo pelo template oficial, não ajustar texto.
- **Existem dois tetos de memória diferentes, não um.** Além do teto de VRAM (afeta o passe
  de refine em resolução alta), há um teto de RAM do host que derruba o processo do ComfyUI
  silenciosamente (sem traceback) em vídeos de ~10s numa tacada só — só apareceu ao tentar os
  257 frames.
- **No regime CFG 1.0, o negative prompt é inerte.** Tudo que o prompt positivo menciona
  (inclusive proibições do tipo "sem legendas") tende a aparecer na tela — o oposto do
  comportamento esperado em CFG alto.
- **O escritor autônomo tem taxa de acerto baixa mesmo em modelo maior (14b).** O Gerald
  original veio de curadoria humana (seeds), não do escritor — isso só ficou claro ao tentar
  reproduzir o sucesso em posts novos e falhar repetidamente.
- **Punchline descritiva passa despercebida por critérios automáticos até alguém assistir ao
  vídeo final.** Nem a rubrica original dos críticos nem os scores capturavam "a piada é só a
  cena, sem reinterpretação" — só o feedback humano no vídeo renderizado revelou isso.
- **Críticos de texto puro são estruturalmente cegos à nuance visual, não apenas rigorosos
  demais.** O sintoma (score 6-7, corte em 8) parecia um problema de calibração de threshold;
  só ficou claro que era um problema estrutural (crítico nunca via a imagem) quando o usuário
  julgou candidatas rejeitadas como "muito boas" olhando as fotos reais.
- **Processos desacoplados sobrevivem a reinícios de sessão; loops de notificação não.**
  `nohup`/`setsid`/`disown` mantêm o trabalho real vivo, mas um waiter próprio de polling
  (`until pgrep ...; sleep; done`) rodado como uma segunda tarefa em background morre
  silenciosamente na mesma transição — isso gerou 3 falsos alarmes de "travou" antes de ser
  diagnosticado. Solução adotada: usar o rastreamento nativo do harness numa única chamada
  (`timeout 3600 <comando>` direto em background), sem encadear um waiter secundário.

## Próximos passos

- [x] Concluir a rodada e2e com timeout de 1h — terminou em ~20 min, 2/15 aprovados.
- [x] Renderizadas as 2 candidatas aprovadas (`data/media-pipeline/e2e-fresh-render/`,
      primeiros vídeos 100% autônomos do pipeline). Feedback do usuário: ambos cortam no
      meio da fala — falta pausa antes/depois da narração dentro dos 5,16 s.
- [x] Tentativa 1 (prompt): pedir pausa explícita antes/depois da fala no prompt de áudio.
      **Resultado negativo, medido objetivamente** (silencedetect + volumedetect): fala ativa
      já em t=0,0s e ainda em t=4,9s de um clipe de 5,16s — padrão de silêncio idêntico
      antes/depois da mudança. O modelo de áudio nativo não obedece pedido de pausa por
      texto; ele preenche a duração inteira com a fala. Prompt revertido mentalmente como
      lever inútil para esse problema (o texto do prompt continua com o pedido de pausa por
      ora, mas não deve ser considerado a solução).
- [x] Tentativa 2 (duração): renderizar o mesmo conceito a 8s/768×448. **Resultado positivo
      parcial, medido objetivamente**: pausa real de ~1,08s no final (fala termina ~6,94s,
      silêncio até 8,02s) — resolve o corte antes do fim. Início ainda sem pausa (fala
      começa em t=0,0s) — a duração maior não corrigiu isso, só o pedido de texto tentou (e
      falhou).
- **Falha de metodologia encontrada pelo usuário**: o teste de 8s usou uma pasta de saída
  nova, e a geração da imagem-base é ela mesma não-determinística — o cavalo sumiu da
  composição por acaso, sem relação com a variável testada (duração). Corrigido fixando a
  imagem-base aprovada (copiada para o novo diretório com o nome esperado, acionando o
  `Reusing completed image` do pipeline) antes de re-testar. **Confirmado**: com a imagem
  fixa, o cavalo volta a aparecer em t=0 e a pausa final se reproduz de forma limpa (~1,3s,
  6,72s→8,02s) — a causa raiz do sumiço era mesmo a regeneração da imagem, não o vídeo.
- **Feedback sobre o texto**: a punchline "TREINAMENTO PARA COEXISTENCIA" é curta/abstrata
  demais — falta uma virada vívida como a do Gerald ("ANTES DE NEGAR SEU EMPRÉSTIMO").
  Usuário sugeriu reescrita com conectivos e reações faladas: "TREINADOR MANDOU FOTO DE
  AMIZADE. MAS A FOTO MOSTRA CAVALO E GATO JUNTOS, HUMMM ESTRANHO... TREINAMENTO PARA
  COEXISTÊNCIA, SERA? RARARARA." (20 palavras vs 14 do original) — hipótese: mais material
  falado preenche a duração com mais naturalidade do que pedir pausa por instrução (que já
  falhou). Testando com a mesma imagem-base fixa e 8s/768×448.
- [ ] Avaliar se 1/15 de aprovação é aceitável para uso rotineiro ou se o escritor precisa de
      mais uma rodada de calibração (few-shot adicional, modelo maior, ou aceitar curadoria
      humana como caminho principal e o escritor como gerador de rascunhos).
- [ ] Corrigir o deslize gramatical notado na piada aprovada ("um humanos comportado").
- [ ] Considerar formalizar no README/CLAUDE.md os defaults comprovados
      (`--concept-timeout 600`, `--humor-second-critic-model qwen2.5vl:7b`,
      `--ltx23-segments` para vídeos >8s).

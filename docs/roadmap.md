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

## Estado atual (2026-07-12)

| Estágio | Situação |
|---|---|
| 1. Seleção de posts | Estável. RSS do Reddit, dry-run disponível. |
| 2. Gate de fonte | Estável. Scores coerentes; r/popular rende pouca matéria-prima visual (dependência de texto); subreddits de fotos/animais rendem ~40-47% de aprovação. |
| 3. Humor (escritor + críticos) | **Em calibração ativa, dois modos de falha mapeados.** Taxa de aprovação orgânica: 0/15 → 0/15 → 1/15 → 2/15 nos últimos 4 replays dos mesmos posts congelados. Falso negativo (crítico cego à imagem subestima piada boa) corrigido com crítico de visão. Falso positivo (crítico aprova com score alto uma virada incoerente com a cena) ainda não corrigido estruturalmente — mitigado caso a caso com revisão humana do texto antes de renderizar. |
| 4. Imagem-base + roteiro | Estável, reaproveitado das runs anteriores sem retrabalho. |
| 5. Render de vídeo (LTX 2.3) | **Resolvido tecnicamente.** Grafo oficial (`workflows/05`) validado; aprovado pelo usuário em 5 s, 8 s e 10,3 s (2 segmentos), e agora também em 9 s / 9,96 s para diálogos mais longos. |
| Pacing do áudio (corte no meio da fala) | **Resolvido.** Causa raiz: duração insuficiente para a contagem de palavras do diálogo, não o modelo ignorando o texto (confirmado por Whisper). Calibração por tentativa+verificação (Whisper + silencedetect) converge em poucas rodadas; ainda não é uma fórmula fechada. |
| Render em posts frescos (fora do Gerald) | **Feito para os 2 conceitos aprovados na run e2e de 2026-07-11.** Cavalo+gato aprovado pelo usuário no áudio/vídeo/pacing. Gato+projetor de galáxia teve o texto reescrito em colaboração com o usuário (piada original fazia sentido zero fora da cena) e o resultado final também foi confirmado ok. |

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
  falhou). Testado com a mesma imagem-base fixa e 8s/768×448.
- **Resultado misto, medido objetivamente**: o texto mais longo de fato preencheu o vazio
  anterior (fala ativa e alta, -6 a -8dB, até 7,9s), mas parece ter passado do ponto —
  praticamente sem pausa sobrando no final (queda brusca só em 8,0s, na borda dos 8,04s do
  clipe). Risco de cortar a risada final "RARARARA" bem na ponta. Boca fechada no último
  frame (inconclusivo visualmente). Vídeo enviado ao usuário para julgar de ouvido.
- **Usuário reportou que o áudio soou igual ao anterior** e questionou a conclusão (com
  razão). Investigação inicial (indireta, por padrão de volume/silêncio) levou a uma
  **conclusão errada** de que o modelo não obedece o texto. Corrigido verificando de fato:
  transcrição com Whisper local dos dois áudios.
  - Texto antigo transcrito: "O treinador mandou foto de amizade. Foto mostra cavalo e gato
    juntos. Treinamento para a coexistência."
  - Texto novo transcrito: "O treinador mandou foto de amizade, mas a foto mostra cavalo e
    gato juntos. Hum, estranho. Treinamento para a coexistência será rararar."
  - **O modelo seguiu o texto novo quase palavra por palavra** — "mas", "hum estranho",
    "será" e "rararar" estão todos presentes. A conclusão anterior ("o modelo não obedece o
    texto") estava errada e foi retirada.
- **Causa raiz real, mais simples**: com o texto mais longo, a fala ocupa quase toda a
  duração de 8s, sem sobrar respiro no final (consistente com a medição de volume anterior,
  agora explicada corretamente) — é só uma questão de contagem de palavras vs. duração do
  clipe, não uma limitação do modelo em seguir o prompt. Ajuste: pequeno aumento de duração
  (200→225 frames, ~9s) para dar folga ao final sem precisar cortar o texto. Em teste, com
  verificação por Whisper (conteúdo) + volumedetect (timing) desta vez, não só um dos dois.
- [x] **Render de 9s (225 frames, 768×448) verificado das duas formas e aprovado pelo
      usuário ("sim, funcionou").** Whisper transcreveu "O treinador mandou foto de amizade,
      mas a foto mostra cavalo e gato juntos, hum, estranho. Treinamento pra coexistência
      será rararara." — praticamente idêntico ao texto pedido. `silencedetect` confirma pausa
      final real de 0,93s (fala ativa até 8,05s, silêncio até 8,98s de um clipe de 9,0s).
      Frame em t=4s confirma o cavalo visível ao fundo (imagem-base fixa reaproveitada
      corretamente). **Conclusão fechada**: para este padrão de dialogo (~20 palavras), 9s é
      a duração que resolve o corte no meio da fala sem esticar demais o clipe. Próximo:
      aplicar o mesmo ajuste (contagem de palavras vs. duração) ao segundo conceito aprovado
      (gato + projetor de galáxia), que ainda só tem o render original de 5s com o mesmo
      problema de corte.
- [ ] **Segundo conceito (gato + projetor de galáxia) em teste com a mesma calibração.**
      Diálogo de 17 palavras (vs. 14 do cavalo original, 20 da reescrita) — interpolando a
      curva palavras→frames observada no cavalo (14→200 quadros/8s, 20→225 quadros/9s), o
      alvo é 217 quadros (~8,68s) a 768×448, com a imagem-base original fixada (evita o
      confound de regeneração já documentado acima). Render em andamento; será verificado
      com Whisper + silencedetect antes de mostrar ao usuário, seguindo a metodologia
      corrigida.
- **Confound de novo, desta vez pego antes de mostrar ao usuário**: o `--output-root` cria
  subpastas por data corrente (`2026-07-12`, não `2026-07-11`); fixar a imagem-base na pasta
  de data errada não aciona o `Reusing completed image` e a imagem foi regenerada do zero de
  novo. Corrigido criando a pasta com a data de hoje e fixando a imagem lá antes de re-rodar
  (`e2e-galaxy-duration-test-v2/2026-07-12/`). Lição: ao fixar imagem-base num teste
  controlado, sempre conferir a data corrente do sistema, não assumir a data da sessão
  anterior.
- **Interpolação linear de palavras→quadros subestimou a duração real necessária.** Com a
  imagem fixada corretamente (teste limpo desta vez), 217 quadros (8,68s) para o diálogo de
  17 palavras do gato+galáxia cortou a fala antes do fim: Whisper transcreveu "Eu sou o
  astrônomo de..." sem o "MIM" final, e `silencedetect` não encontrou nenhum silêncio de
  cauda (fala ativa até o corte, -13,4dB médio nos últimos 0,68s). Essa piada tem 3 frases
  curtas com pausas internas longas entre elas (~2,9s de pausa somada nos primeiros 6,9s) —
  aparentemente consome mais tempo por palavra do que a curva do cavalo sugeria. Tentativa
  seguinte: 241 quadros (~9,64s), com folga maior de propósito (cortar a piada é pior do que
  sobrar silêncio).
- **Terceiro confound na mesma pasta: cache de vídeo por nome de arquivo, não por parâmetros.**
  Rodar de novo com `--ltx23-frames 241` na mesma pasta (mp4 de 217 quadros já presente)
  produziu "Reusing completed video" — o pipeline reaproveita o mp4 existente pelo nome,
  ignorando que a contagem de quadros pedida mudou. `ffprobe` confirmou 8,68s (o antigo), não
  9,64s. Corrigido apagando o mp4 (e os stills derivados) antes de re-rodar. Lição: ao iterar
  duração/quadros num teste controlado na mesma pasta, sempre apagar o mp4 anterior — só a
  imagem-base deve ser fixada, nunca o vídeo.
- [x] **Render de 9,64s (241 quadros) do gato+galáxia verificado nas duas formas e enviado ao
      usuário.** Whisper transcreveu o diálogo completo, incluindo o "de mim" final que tinha
      sido cortado na tentativa anterior. `silencedetect` confirma ~2,1s de silêncio real no
      fim (fala termina ~7,54s de um clipe de 9,64s), sem corte no meio. Frame em t=3s
      confirma o gato e a luz do projetor de galáxia visíveis (imagem-base reaproveitada
      corretamente). Aguardando julgamento do usuário sobre o ritmo (pausa final talvez
      generosa demais, mas mais segura do que cortar a piada).
- **Feedback do usuário sobre o gato+galáxia**: "audio e video perfeitos... o texto com o
  video e audio não é engraçado, não tem nenhum sentido". Pacing e execução técnica
  resolvidos, mas a piada em si ("EU ABRI O BRASIL. O GATO FEZ UMA GALAXIA DE SONO. EU SOU O
  ASTRONOMO DE MIM.") foi julgada sem sentido/sem graça — mesmo tendo passado com consenso
  dos dois críticos (scores 8-9, incluindo o crítico de visão). Este conceito já tinha sido
  marcado como suspeito no item 13 acima ("abertura genérica reciclada — revisar antes de
  render") e o alerta se confirmou. **Achado novo, complementar ao anterior**: a correção do
  crítico de visão resolveu o problema de subestimar piadas boas (falso negativo), mas não
  protege contra o crítico aprovar uma piada incoerente com confiança alta (falso positivo) —
  os dois modos de falha do funil de humor são distintos e a correção de um não resolve o
  outro. Reabre a pergunta pendente sobre se a aprovação orgânica do escritor (1-2/15) é
  confiável o bastante para uso sem revisão humana do texto antes de renderizar.
- **Reescrita colaborativa do texto**: usuário apontou a causa raiz específica —
  "Brasil"/"galáxia"/"astrônomo" não têm nada na cena real (só um gato sonolento com fundo
  azul-escuro de pontinhos de luz do projetor). Duas opções propostas ancoradas na cena
  visível de fato (padrão Gerald: o gato finge um papel, mas a realidade é mais simples);
  usuário escolheu a Opção B: **"GANHOU UM CÉU ESTRELADO SÓ PRA ELE. FINGE QUE TÁ
  CONTEMPLANDO O UNIVERSO. NA VERDADE VAI DORMIR EM 5 MINUTOS."** (20 palavras).
- [x] **Render final (249 quadros, ~9,96s, 768×448) verificado nas duas formas e enviado.**
      Whisper confirma o punchline completo ("na verdade, vai dormir em cinco minutos").
      `silencedetect` confirma ~2,65s de silêncio real no fim (fala termina ~7,32s) — sem
      corte. Aumentei a duração de 241→249 quadros por precaução (texto 3 palavras mais longo
      que a tentativa anterior que já tinha precisado de mais tempo que o previsto).
      Aguardando julgamento do usuário sobre se a piada agora faz sentido.
- [x] **Usuário confirmou**: "continuo vendo o gato, fundo azul.. texto e audio estão ok."
      Os dois conceitos aprovados na run e2e de 2026-07-11 (cavalo+gato e gato+galáxia) estão
      agora com pacing de áudio correto e piada coerente com a cena, ambos verificados
      objetivamente (Whisper + silencedetect) e confirmados pelo usuário.
- [ ] Avaliar se 1/15 de aprovação é aceitável para uso rotineiro ou se o escritor precisa de
      mais uma rodada de calibração (few-shot adicional, modelo maior, ou aceitar curadoria
      humana como caminho principal e o escritor como gerador de rascunhos).
- [ ] Corrigir o deslize gramatical notado na piada aprovada ("um humanos comportado").
- [x] **Defaults comprovados formalizados no README** (`--humor-second-critic-model
      qwen2.5vl:7b`, `--concept-timeout 600`, `--ltx23-segments` para >8s, envelope de
      memória VRAM/RAM, fluxo de calibração de pacing com Whisper + silencedetect e o aviso
      sobre o cache de vídeo por nome de arquivo). Commit `6050705`.
- [x] **Hardening estrutural do falso positivo do funil de humor** (commit `6050705`): novo
      "teste de ancoragem visual" na rubrica dos críticos — a virada precisa apontar para algo
      literalmente visível na cena; se usa um conceito abstrato sem pista visual
      correspondente (o caso real do "Brasil"/"astrônomo"), `visual_payoff` é limitado a 4.
      Mesma regra adicionada ao prompt do escritor, com o caso do gato+galáxia como
      contraexemplo concreto. Suíte de 28 testes continua passando; ainda não validado com uma
      rodada e2e nova (só a mudança de prompt/rubrica, sem replay dos 15 posts congelados).
- [ ] Deslize gramatical ("um humanos comportado") **não localizável** — o dado de origem
      (`concepts.json` daquela run específica) já foi sobrescrito por execuções posteriores
      nas mesmas pastas de saída. Item removido do escopo ativo; sem ação possível sem
      reproduzir a run original.
- [ ] Replay dos 15 posts congelados com a rubrica de ancoragem visual nova, para medir se o
      hardening reduz falsos positivos sem reintroduzir os falsos negativos que o crítico de
      visão já corrigiu (risco: uma virada legítima mas com metáfora indireta pode ser
      penalizada demais — vigiar isso no próximo lote).
- [ ] Avaliar se 1-2/15 de aprovação orgânica (taxa observada antes deste hardening) ainda é
      aceitável para uso rotineiro, ou se compensa manter curadoria humana como caminho
      principal e o escritor como gerador de rascunhos.
- **Decisão do usuário (2026-07-15): `r/popular` vira fonte fixa, não-negociável**, revertendo
  a preferência anterior por subreddits visuais (que tinham ~40-47% de aprovação no gate de
  fonte vs. 0/10 do `r/popular` testado em 2026-07-08). Em vez de rodar o funil inteiro num
  lote fixo e descartar o que reprovar, a abordagem agora é curadoria progressiva: avaliar
  cada post do `r/popular`, pular o que não bate o mínimo, e acumular um backlog de 20
  aprovados ao longo de várias execuções — não numa única chamada.
- **Dois obstáculos técnicos confirmados antes de implementar**: (1) o RSS de `r/popular`
  devolve só 25 entradas por busca, sem paginação implementada — uma amostra ao vivo rendeu
  13 imagem / 9 vídeo / 3 texto; (2) o gate de fonte (`assess_source_suitability`) só aceita
  `media_type == "image"` hoje — vídeo/texto são auto-rejeitados porque o motor de render é
  I2V (imagem→vídeo), sem caminho pra outros tipos de mídia. Usuário decidiu: pular vídeo/texto
  por enquanto (não investir em extrair frame de vídeo agora) e acumular o backlog entre
  execuções (não implementar paginação do RSS agora).
- [x] **Novo script `scripts/reddit_popular_curation.py`**: busca o feed de `r/popular`,
      pula posts já vistos (aprovados OU rejeitados) em execuções anteriores via um arquivo
      de backlog persistente (`data/media-pipeline/popular-curated-backlog.json`, gitignored),
      avalia só posts de imagem novos com a mesma descrição de visão + gate de fonte do
      pipeline principal, e acumula aprovados até `--target` (default 20). Vídeo/texto contam
      como "vistos" mas nunca chamam o modelo de visão. 2 testes novos
      (`PopularCurationBacklogTests`): posts de vídeo/texto não chamam o modelo; posts já
      vistos não são reavaliados numa segunda chamada. Suíte total: 30 passed.
- **Bug pego na primeira execução real**: rodei com `timeout 900` e sem `-u`; o processo
  bateu o timeout (exit 124) SEM imprimir nada (stdout bufferizado, `timeout`/SIGTERM não
  flusha o buffer) E sem salvar nada (o backlog só era gravado uma vez no final) — toda a
  janela de ~15 min processando imagens foi perdida sem deixar rastro. Corrigido: salvar o
  backlog a cada post avaliado (não só ao final) e `flush=True` em todo print de progresso.
  Lição: em qualquer script novo de longa duração, checkpoint incremental + stdout sem buffer
  não são opcionais — sem isso um timeout ou kill perde trabalho de forma silenciosa.
- **Primeira tentativa de replay (`e2e-visual-anchor-hardening/2026-07-15`) invalidada por
  erro de metodologia próprio**: esqueci `--limit 15` no comando; o default é `--limit 10`, e
  `load_frozen_posts(args.posts_file)[:args.limit]` simplesmente trunca a lista congelada —
  processou só os 10 primeiros dos 15 posts, faltando 5, incluindo o "employee of the month"
  (um dos casos historicamente fortes). Resultado 0/10 não é comparável ao 2/15 anterior;
  descartado. Corrigido e relançado com `--limit 15` explícito
  (`e2e-visual-anchor-hardening-v2`).
- [x] **Replay v2 (15 posts completos) concluído: 0/15 aprovados.** Antes de concluir que o
      hardening piorou o funil, inspecionei as avaliações: 7/15 rejeitados por "críticos sem
      consenso" (os dois críticos aprovam candidatas DIFERENTES na mesma rodada — o padrão de
      falha histórico, já visto em 2026-07-09 antes de qualquer hardening novo), e só **1
      avaliação em toda a rodada** bateu no teto novo de ancoragem visual (`visual_payoff=4`,
      post "Unexpected", motivo "punchline não está relacionada à cena" — caso correto, não
      falso positivo). O caso "employee of the month" (que já tinha sido aprovado com
      consenso antes) teve candidatas com scores altos dos dois críticos individualmente
      (llama3 aprovou a candidata 3 com 8/9/9/8/8; qwen2.5vl aprovou a candidata 5 com
      8/9/10/10/9) mas SEM consenso entre eles sobre qual candidata — não foi penalizado pela
      regra de ancoragem visual. **Conclusão**: 0/15 está dentro da variância já observada
      antes do hardening (0/15, 0/15, 1/15, 2/15 em replays anteriores do mesmo lote); não há
      evidência de que a rubrica nova tenha reintroduzido falsos negativos. O gargalo
      dominante continua sendo consenso entre os dois críticos, não a rubrica de ancoragem.

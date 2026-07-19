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

## Estado atual (2026-07-16)

| Estágio | Situação |
|---|---|
| 1. Seleção de posts | Estável. `r/popular` como fonte fixa (decisão de produto), RSS com `?limit=100`, curadoria progressiva com backlog persistente (`scripts/reddit_popular_curation.py`). |
| 2. Gate de fonte | **Endurecido e validado.** Booleanos explícitos no schema + tetos determinísticos no código (legenda embutida / colagem → rejeição). Verificado contra a colagem real que escapava e contra imagem boa (sem falso positivo novo). Disparando corretamente em produção. |
| 3. Humor (escritor + críticos) | **Operacional com revisão obrigatória.** Rendimento orgânico ~10% (2/20 ciclo 1, 1/9 ciclo 2 com gate mais rígido). Revisão de texto pelo Claude antes de render é etapa padrão (critério: até 2 correções, depois descarte). Checkpoint incremental protege lotes contra timeout/kill. |
| 4. Imagem-base + roteiro | Estável, com limitação mapeada: a geração preserva presença de objetos/ações, não relações de tamanho nem identidade de espécie — piadas devem ancorar no que sobrevive. Prior forte de "gato laranja" observado (3 ocorrências). |
| 5. Render de vídeo (LTX 2.3) | **Resolvido.** Grafo oficial validado em 5–10,3s; calibração palavras→duração por tentativa+verificação (Whisper + silencedetect) convergindo em 1-2 rodadas. |
| 6. Pacote de publicação (Fase 1) | **Implementado.** `publish` no contrato v3, `final_916.mp4` blur-pad, curadoria prioriza retrato, Telegram com caption colável. Fases 2–3 (métricas e loop de feedback) não iniciadas. |
| Execução de runs longas | **Resolvido** (após kills recorrentes do ambiente de background): processo desacoplado (`setsid nohup` + log) vigiado por Monitor nativo do harness cobrindo os dois estados terminais. |
| Produção | **5 vídeos entregues ao usuário até agora**: cavalo+gato e gato+galáxia (aprovados), Birdie, gato de feltro e resgate do lobo (aguardando veredito). |

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
- [x] **Primeira rodada real com o fix: 6/20 aprovados.** 25 posts buscados no `r/popular`,
      22 elegíveis por serem imagem (3 vídeo/texto pulados), 6 aprovados no gate de fonte
      (27% — bem acima do 0/10 observado num teste anterior, provavelmente por mistura de
      conteúdo diferente no momento da coleta, não por mudança de critério). Aprovados:
      "I tie it like a belt.", "The first thing she does every morning", "Locomotive nearly
      engulfed flames in Ontario fires.", "One has brains and the other one has tattoos",
      "just couldn't resist it", "Adopted this little guy yesterday...". Backlog persistido
      corretamente em `data/media-pipeline/popular-curated-backlog.json`. Faltam 14 pra
      fechar 20 — rodar `scripts/reddit_popular_curation.py --target 20` de novo mais tarde
      (o feed muda com o tempo; rodar de novo agora tende a repetir os mesmos 25 já vistos).
- **Achado que mudou o plano**: o feed RSS do Reddit aceita `?limit=` — testado ao vivo,
  `?limit=100` devolve 100 entradas (vs. 25 do default que o código usava), `?limit=250+`
  dá 429 (rate limit; 100 é o teto real do endpoint, documentado pela própria API do Reddit,
  não um bug nosso). Corrigido: `feed_url`/`fetch_feed_once`/`fetch_feed` ganharam parâmetro
  `limit` (default 100), exposto como `--rss-limit` nos dois scripts. Isso muda a estratégia:
  não é mais necessário espalhar a curadoria por cron/múltiplos dias — um lote de ~100
  entradas por busca já tem material suficiente pra fechar 20 aprovados em poucas execuções
  seguidas no mesmo dia.
- [x] **Backlog fechado: 20/20 aprovados**, em 3 execuções seguidas do mesmo lote de 98
      entradas (buscado uma vez com `?limit=100`; execuções seguintes só continuaram
      avaliando os posts ainda não vistos daquele mesmo lote, sem buscar de novo). Duas delas
      bateram no timeout de 25 min (cada avaliação de imagem leva ~30-40s: descrição de
      visão + gate de fonte, dois round-trips ao Ollama) mas o checkpoint incremental
      preservou o progresso todas as vezes — sem perder trabalho. Taxa de aprovação final:
      20/~60 imagens avaliadas (~33%), consistente com a taxa observada no primeiro teste
      (27%). Lista completa dos 20 títulos aprovados em
      `data/media-pipeline/popular-curated-backlog.json`.
- [ ] **Funil de humor rodando nos 20 curados do `r/popular`** (sem render). Backlog
      convertido para o formato de `--posts-file` (só o campo `post` de cada entrada
      aprovada) em `data/media-pipeline/popular-curated-backlog-selected.json`. Rodando com
      os defaults calibrados (`--humor-model qwen3:14b`, `--concept-timeout 600`,
      `--humor-second-critic-model qwen2.5vl:7b` já é default). Expectativa baseada no
      histórico da sessão: 2-4 aprovados em 20 (~10-15%). Próximo passo depois de concluir:
      revisão humana do texto de cada aprovado antes de renderizar (lição do gato+galáxia —
      consenso alto não garante piada coerente).
- [x] **Funil concluído: 2/20 aprovados (10%)**, dentro da faixa prevista. "Birdie trying out
      his new set of wheels" (cão paraplégico + cadeira de rodas) e "I made a life-size
      needle-felted cat!" (escultura de feltro).
- **Revisão humana encontrou 1 problema real, 1 falso alarme meu**: a piada do gato de feltro
  tinha lógica invertida (afirmava "não é de fibra, é de fato/real" quando o post original é
  sobre uma escultura que PARECE real). Eu também suspeitei que a legenda queimada na imagem
  do cão ("Trying to help the paralyzed dog...") + marca d'água do TikTok fosse um problema
  estrutural — **verifiquei e não é**: a descrição visual gerada pelo modelo nunca menciona a
  legenda, e o prompt de imagem já pede explicitamente "no readable text, labels, watermarks"
  — a etapa de imagem-base limpa já filtra isso por design. Retratei essa suspeita.
- **Novo critério do usuário, salvo como padrão permanente**: autonomia para tentar corrigir
  até 2x um problema de texto detectado numa piada aprovada, descartando se continuar ruim
  depois disso — em vez de sempre perguntar antes de tentar. Ver memória
  `joke-fix-retry-limit`.
- [x] **Correção aplicada (tentativa 1/2) ao gato de feltro**: "GATO PARADO NA MESA DE
      MADEIRA. OLHAR DESCONFIADO, PELO PERFEITO. NA VERDADE É TODO FEITO DE FELTRO." — lógica
      corrigida (parece real, mas é feltro) e abertura genérica reciclada ("EU ABRI O MUNDO")
      trocada por âncora real na cena (mesa de madeira, postura parada). Salvo em
      `data/media-pipeline/popular-humor-funnel/2026-07-15/approved-two-fixed.json`, pronto
      para retomar via `--approved-concepts-file` e renderizar os dois.
- **Correção de rumo do usuário**: apliquei a correção do texto e voltei a perguntar "quer
  que eu siga pro render?" — o usuário apontou que isso contradiz a própria autonomia que
  acabou de dar (corrigir → julgar → seguir sozinho, sem parar pra pedir permissão de novo).
  Lição aplicada imediatamente: segui direto para o render sem novo gate de confirmação.
- [x] **Primeiro render de posts do `r/popular` concluído: 2 vídeos, verificados e
      entregues.** 241 quadros (9,64s) usado direto (mesmo padrão de 3 frases do
      gato+galáxia), sem repetir a subestimativa de duração já documentada acima. Um retry
      necessário: primeira tentativa falhou no preflight por timeout pontual de leitura no
      ComfyUI (5s), confirmado saudável logo depois (`/system_stats` respondeu rápido, GPU
      livre) — segunda tentativa rodou de ponta a ponta sem erro. Ambos os vídeos passaram
      nas duas verificações (Whisper: texto completo, sem corte; `silencedetect`: ~2s de
      pausa final real nos dois) e foram entregues ao usuário.
- [ ] **Segunda rodada do funil nos 18 posts rejeitados** (usuário mandou seguir sem pausa
      pra decidir): amostra estocástica nova do escritor (`qwen3:14b`, mesmos posts, mesmo
      gate de fonte) pode aprovar diferente do que a primeira rodada, como já visto no caso
      "employee of the month" (rejeitado numa amostra, aprovado de primeira noutra). Rodando
      em `data/media-pipeline/popular-humor-funnel-retry/`.
- **Bug confirmado no pipeline principal (não só no script de curadoria)**: o timeout de 1h
  bateu no post 18/18 (última rodada de crítico) e **nada foi salvo** — `generate_concepts()`
  processa o lote inteiro numa chamada só, e `persist_concepts()` só é chamado depois que ela
  retorna. Diferente do `reddit_popular_curation.py` (que já tem checkpoint incremental), o
  pipeline principal ainda não tem — mesma classe de bug, escopo maior (perde o lote inteiro,
  não só o post em andamento). Não refatorado agora (mudança maior no fluxo central); mitigado
  dividindo os 18 restantes em 2 lotes de 9 (cada um roda bem dentro de 1h, ~4-6min/post).
  Considerar adicionar checkpoint incremental a `generate_concepts()` como item futuro.
- **3 kills seguidos do lote A em background** (não timeout, não OOM, sem traceback) — parei
  de insistir sozinho após a 3ª e perguntei ao usuário como prosseguir; ele mandou tentar de
  novo. 4ª tentativa rodou de ponta a ponta sem problema. Causa provável: reciclagem do
  próprio ambiente de sessão em background, não um bug do pipeline.
- [x] **Lote A concluído: 2/9 aprovados.** "One has brains and the other one has tattoos"
      (Neymar relógio vs Haaland livro) e "Watching the semifinal on a flatscreen tv in the
      hotel."
- **Segundo problema estrutural de fonte encontrado (não é texto, ver critério em
  `joke-fix-retry-limit`)**: a imagem do "brains vs tattoos" é o post inteiro do Neymar/Haaland
  com legenda embutida — o meme só faz sentido lendo a legenda (quem é quem, os preços), e são
  duas fotos/pessoas diferentes que a piada gerada tratou como uma só ("EU DEI O LIVRO ...
  TROQUEI CULTURA POR LUXO", narrativa em 1ª pessoa que não corresponde à cena real de duas
  pessoas distintas). Isso deveria ter reprovado no gate de fonte (`text_independence`), não
  passou. **Descartado** — não é um problema de texto corrigível por reescrita, é a mídia-fonte
  que não serve. A do hotel/TV está ancorada na cena real, sem esse problema — segue para
  render como está.
- [x] **Lote B concluído: 0/9.** Fechamento da retentativa nos 18 rejeitados: 2/18 aprovados
      pelo funil, 1 descartado (defeito estrutural de fonte), 1 seguiu adiante. Total das
      duas passadas nos 20 curados: 4 aprovações brutas do funil (2+2), 3 conceitos úteis
      após revisão (Birdie, gato de feltro corrigido, hotel/TV corrigido).
- **Correção de texto no hotel/TV (tentativa 1/2, dentro da autonomia concedida)**: o texto
  aprovado ("ERA SÓ UMA TAREFA / MAS O FUTEBOL SE TORNOU UM DEUS / AGORA EU SOU O REI DA
  TELA") tinha o mesmo defeito do gato+galáxia — abstrações sem âncora na cena. Reescrito
  ancorado no visível: **"SEMIFINAL DA COPA NO HOTEL. A TV É MENOR QUE O QUADRO NA PAREDE.
  MAS HOJE ELA É O MAIOR TELÃO DO MUNDO."** (23 palavras → 249 quadros/9,96s, faixa já
  comprovada). Render em andamento em `data/media-pipeline/popular-render-batch2/`.
- **Modo de falha novo encontrado no 1º render do hotel/TV**: áudio e pacing perfeitos
  (Whisper: texto completo; 1,2s de pausa final), mas a imagem-base gerada divergiu da
  cena-fonte — inventou um gato, fez a TV GRANDE (a fala diz "a TV é menor que o quadro",
  falso na cena renderizada) e pôs pseudo-texto na tela. **Lição: piada ancorada em relação
  espacial/tamanho relativo ("menor que o quadro") é frágil — a geração estocástica da
  imagem-base preserva presença de objetos, não relações de tamanho entre eles.** Âncoras
  robustas são existência/identidade de objetos e ações, não comparações visuais. Tentativa
  2/2 (dentro do critério de autonomia): imagem e vídeo apagados, re-render com nova amostra
  da imagem-base; se divergir de novo, descarte.
- [x] **Tentativa 2/2 divergiu de novo (TV grande, gato espúrio pela 2ª vez, pseudo-texto num
      copo) → conceito hotel/TV descartado**, conforme o critério de 2 tentativas. A âncora
      de tamanho relativo não sobrevive à amostragem da imagem-base — confirma a lição acima
      com N=2.
- **Fechamento do ciclo dos 20 posts curados do `r/popular`**: 4 aprovações brutas do funil
  em 2 passadas (2+2), 2 descartes na revisão/produção (fonte dependente de legenda;
  âncora visual frágil), **2 vídeos finais entregues** (Birdie, gato de feltro corrigido).
  Rendimento líquido: 2/20 posts curados viraram vídeo aprovável (10%), com 3 modos de falha
  distintos documentados no caminho (texto incoerente corrigível, fonte estruturalmente
  inadequada, âncora visual não-preservada pela geração).
- [x] **Checkpoint incremental em `generate_concepts()`** (pendência antiga): novo parâmetro
      `checkpoint` chamado após cada conceito concluído; o `main` passa um callback
      best-effort que persiste `concepts.json` parcial (falha de checkpoint nunca aborta o
      lote — `persist_concepts` com `zip` aceita lista parcial naturalmente). Elimina a
      classe de perda "timeout no post 18/18 perde o lote inteiro". **Primeira delegação real
      ao modelo local**: o teste unitário foi rascunhado pelo `qwen3-coder:30b` via Ollama
      (spec fechada, sem rede no teste); revisão encontrou e corrigiu 1 bug do rascunho
      (`concept.humor_approved` em vez de `concept["humor_approved"]` — conceitos são dicts).
      Suíte: 31→33 passed.
- [x] **Gate de fonte endurecido contra colagens/legendas embutidas** (caso Neymar/Haaland):
      endurecer só o prompt **não funcionou** (regressão real contra a imagem original ainda
      aprovava com `text_independence=3`, na borda do corte). Solução na filosofia do projeto:
      o modelo responde dois booleanos explícitos no schema (`embedded_text_carries_meaning`,
      `multi_photo_collage`) e os tetos são impostos deterministicamente em
      `finalize_source_suitability_review` (legenda→text_independence≤2; colagem→além disso
      visual_clarity≤3). Verificado empiricamente: a colagem real agora é rejeitada; imagem
      boa (gato de feltro, foto única) continua aprovada. +2 testes determinísticos.
- [x] **Decisão de fluxo formalizada** (pendência estratégica): o funil autônomo fica como
      está (~10% de rendimento líquido é aceitável para matéria-prima do `r/popular`), e a
      **revisão de texto pelo Claude antes de qualquer render vira etapa padrão do fluxo** —
      com o critério já acordado de até 2 tentativas de correção e descarte se persistir
      (ver memória `joke-fix-retry-limit`). Justificativa empírica: essa revisão salvou 1 dos
      3 conceitos úteis (gato de feltro) e evitou 2 renders desperdiçados (Neymar/Haaland
      teria falhado na tela; hotel/TV falhou mesmo com revisão, mas o descarte custou 2
      renders em vez de virar entrega ruim).
- [ ] **Ciclo 2 de produção em andamento**: curadoria nova do `r/popular` (feed rodou de um
      dia pro outro) rendeu **9 aprovados de 70 imagens avaliadas** — e o gate endurecido já
      disparou em produção (rejeição do post do Zelensky com o motivo determinístico "texto
      embutido carrega o significado do post"). Backlog acumulado: 29. Funil de humor rodando
      nos 9 novos, agora protegido pelo checkpoint incremental.
- **Ambiente de background voltou a matar processos (3 kills seguidos no funil do ciclo 2)**.
  Mudança de arquitetura de execução, combinando as duas lições da sessão: o trabalho real
  roda como **processo desacoplado** (`setsid nohup`, imune à reciclagem do ambiente; log em
  arquivo estável) e a notificação fica com um **Monitor nativo do harness** que cobre os
  dois estados terminais (conclusão via EXIT_CODE no log, OU processo morto sem terminar) —
  sem waiter próprio, que era a parte frágil do padrão antigo. Funcionou de primeira: funil
  completou de ponta a ponta na 1ª execução desacoplada.
- [x] **Funil do ciclo 2: 1/9 aprovado** ("Respect for this guy. Saved a wolf from a trap...").
      O gate endurecido re-avaliou na entrada e rejeitou 4 dos 9 que a curadoria (gate antigo)
      tinha aprovado — filtro mais rígido agindo como esperado. O texto aprovado tinha
      problemas claros de PT-BR ("LÓBO", "TRAPAÇA" como falso cognato de trap, "CAMPESINATO"
      deslocado) e punchline descritiva. Reescrito (tentativa 1/2) ancorado na cena real
      (lobo com corrente na pata rosnando pra vara de resgate): "PRESO NA ARMADILHA, ELE
      ROSNA. RECUSA AJUDA COM ESTILO. CLIENTE DIFÍCIL ATÉ NO RESGATE." (14 palavras → 225
      quadros/9s). Render em andamento.
- [x] **Render do lobo verificado e entregue** (whisper verbatim; fala até 7,3s, 1,69s de
      pausa final; cena coerente com a fala). Ressalva reportada ao usuário: a geração trocou
      a espécie (lobo → animal alaranjado tipo gato). Como a reescrita não nomeia a espécie
      ("ELE rosna"), o vídeo fica internamente coerente — mais uma confirmação da lição de
      ancoragem: identidade de espécie também é âncora frágil; o que sobrevive de forma
      confiável é ação+objeto (animal preso rosnando pra vara), não a taxonomia.
- **Padrão empírico recorrente: gato laranja espontâneo.** 3ª ocorrência de gato/animal
  alaranjado aparecendo sem ser pedido na imagem-base (hotel/TV render 1: gato inteiro;
  render 2: cabeça de gato; lobo: espécie trocada pra felino laranja). O modelo de imagem tem
  um prior forte nessa direção — considerar mencionar explicitamente a espécie/aparência no
  image_prompt quando a identidade do animal importar.
- **Veredito do usuário nos 3 vídeos do dia: qualidade muito pior, "parece apresentação de
  foto parada mudando de ângulo" — volta atrás/retry.** Investigação sistemática antes de
  re-renderizar, hipóteses testadas em ordem: (1) resolução da imagem-base — FALSIFICADA
  (768×768 idêntico entre aprovados e novos); (2) movimento medido por diff de pixels —
  invertido: os novos têm MAIS mudança global (53-86% vs 17-34% dos aprovados), consistente
  com câmera derivando sobre cena estática em vez de sujeito animado; (3) video_script/prompt
  — idênticos em estrutura; (4) seed — aleatório por tempo em todos, não é revertível.
  **Causa raiz real: processo, não código.** Os vídeos aprovados (cavalo, galáxia) usavam
  imagens-base FIXADAS que haviam passado por vetting humano; os 3 novos renderizaram
  imagens-base recém-geradas sem ninguém olhar (Birdie: cão pulando na coleira SEM usar o
  equipamento de rodas — âncora da piada falsa na cena; gato de feltro: composição surreal
  com prancha flutuante e tablet aleatório; lobo: espécie trocada). Eu verifiquei áudio e
  pacing dos 3, mas só inspecionei imagem do lobo — pulei a inspeção visual de 2 dos 3.
  **Correção de processo: inspeção da imagem-base é etapa obrigatória de verificação antes
  de entregar, no mesmo nível de Whisper e silencedetect.** Retry dos 3 em andamento com
  amostragem nova.
- [x] **Retry com inspeção: Birdie e gato de feltro aprovados na verificação completa e
      reentregues.** Birdie melhorou visivelmente (cão de fato usando o equipamento de rodas,
      movimento real de sujeito nos frames). Gato de feltro coerente com ressalva menor
      reportada (fala diz "mesa de madeira", cena mostra barra/poleiro de madeira).
- [x] **Lobo descartado** pelo critério de 2 tentativas: o resample trocou a espécie DE NOVO
      (gato rosnando, 4ª ocorrência do prior felino) e as âncoras degradaram (sem armadilha
      prendendo o animal, sem vara de resgate — "recusa ajuda" ficou sem suporte visual).
      Detalhe importante: o prompt de cena dizia "Um lobo" explicitamente, duas vezes, em
      português — e o modelo de imagem gerou felino mesmo assim. Hipótese para investigação
      futura: a descrição visual embutida no image_prompt está em português; termos de
      espécie em inglês ("gray wolf") no prompt (que a regra do projeto já diz que deve ser
      em inglês) provavelmente pesariam mais que "lobo". Candidato a fix em
      `compose_image_prompt`/`build_video_script`: traduzir ou duplicar a espécie/aparência
      do sujeito em inglês explícito quando a identidade importar.
- **Saldo do dia após o retry**: 2 vídeos válidos do ciclo r/popular reentregues com o
  processo de verificação completo (Birdie, gato de feltro); 2 conceitos descartados com
  critério (hotel/TV: âncora de tamanho; lobo: identidade de espécie). O funil de produção
  segue operacional; próxima matéria-prima quando o feed rodar.
- **Usuário reprovou também as versões do retry ("continua imagem congelada com movimento de
  câmera; antes estava bem melhor") — meu diagnóstico anterior estava incompleto.** O vetting
  da imagem-base era necessário mas não suficiente. Investigação nova, com diff do prompt
  LTX compilado (função é determinística, reconstruída localmente para aprovado vs
  reprovado): os prompts são estruturalmente IDÊNTICOS, incluindo "minimal motion" e "very
  slow push-in" — o template genérico de `build_video_script` ("fixed stare, blinks once,
  tiny ear twitch") vale pra todos. **Diagnóstico refinado: os aprovados (cavalo, galáxia)
  eram close/medium de um rosto, onde o modelo de A/V nativo anima piscada/boca junto com o
  voice-over — movimento de sujeito perceptível de graça; os novos são cenas abertas (praia,
  poleiro), onde o mesmo template só rende deriva de câmera → sensação de foto animada.**
  Alavanca identificada sem tocar código: `timeline[0]` e `character` do video_script são
  DADOS que entram direto no prompt — reescritos com ação explícita de sujeito (Birdie: cão
  avança no carrinho de rodas, cauda abanando; feltro: mão entra em quadro e faz carinho, o
  gato NÃO reage — movimento que ainda reforça a piada) e câmera estática (sem push-in).
  Teste de variável única em andamento (mesmas imagens-base, só o spec de movimento mudou).
- [x] **Teste de movimento validado nos frames e entregue**: com ação explícita no
      `timeline`/`character`, o Birdie ganhou movimento real de sujeito (cão muda de postura,
      sobe no carrinho, mulher caminha) e o gato de feltro ganhou um evento em cena (mão
      entra, faz carinho, sai — gato imóvel, reforçando a piada). Áudio verbatim e pausas ok
      nos dois. **Se o usuário aprovar, o fix estrutural é fazer `build_video_script` gerar
      timeline/character com ação específica da cena (derivada da descrição visual) em vez do
      template genérico "fixed stare / blinks once / minimal motion"** — o template só
      funciona por acaso em close de rosto.
- **Veredito final do usuário (2026-07-18) nos 2 vídeos: reprovados nos 3 eixos** — "piada
  sem punch, áudio é uma descrição do vídeo, vídeo é imagem estática com câmera em
  movimento". Os dois conceitos foram **descartados** (2+ tentativas consumidas cada, pelo
  critério). Diagnóstico unificado dos 3 eixos: (1) os diálogos que o usuário aprovou antes
  eram um NARRADOR COM ATITUDE (suspeita, ironia, reação: "hummm estranho... será?", "finge
  que tá contemplando") — os reprovados eram inventário neutro da cena, por isso soam como
  audiodescrição; (2) piada descritiva e áudio-descritivo são o mesmo defeito; (3) cena
  aberta continua rendendo vídeo estático mesmo com spec de ação — o LTX só anima com vida
  rosto/expressão em close/plano médio, ou elementos intrinsecamente móveis (fogo, água).
- [x] **Recalibração do funil nos 3 eixos** (commit desta entrada): regra determinística nova
      em `humor_candidate_issues` (overlap de setup+escalada com a fonte ≥60% → "falta
      narrador com opinião"); prompt do escritor com a regra da voz narrativa + o diálogo do
      cavalo (aprovado pelo usuário) como 3º exemplar de produção; rubrica dos críticos com
      "teste de voz narrativa" (audiodescrição → laugh/surprise ≤4); gate de fonte pontuando
      `motion_potential` pelo que o I2V anima de verdade (close com rosto ou elemento móvel;
      cena aberta com sujeitos distantes ≤2). Suíte: 35 passed (2 testes novos, incluindo o
      diálogo do cavalo passando e o do Birdie sendo rejeitado pelo check novo).
- **Mudança de processo para o próximo ciclo**: mostrar o TEXTO das piadas aprovadas ao
  usuário ANTES de qualquer render — validação de texto é barata e instantânea; render é
  caro. Três lotes seguidos de render desperdiçado teriam sido evitados com esse gate.
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
- [x] **Baseline de qualidade A/V registrado a pedido do usuário** (antes de avançar pro
      ciclo 3): os dois renders com veredito "audio e video perfeitos" copiados para
      `data/media-pipeline/reference-baseline/` (galáxia 9,64s e cavalo 9s, vídeos +
      imagens-base), com a receita reproduzível documentada na memória
      `av-quality-baseline`: sujeito único em close/plano médio com rosto visível, imagem-base
      vetada, 768×448/225-249 quadros, narrador com atitude, fala com ≥1s de cauda. Todo
      render novo deve ser comparado contra esses arquivos antes de ser entregue.
- [x] **Ciclo 3, veredito de texto do usuário via página de revisão (Artifact)**: os 4 textos
      propostos foram aprovados ("estão todos aprovados, gostei mais do 4 AIRBNB"). Fluxo novo
      validado: candidatas do funil (0/15 por consenso) recuperadas dos `rounds` persistidos,
      filtradas/reescritas por mim no padrão narrador-com-opinião, e apresentadas com o post
      original lado a lado numa página HTML — veredito de texto barato antes de qualquer GPU.
- **Consulta do usuário sobre viés ("muito bichinho — certeza que é r/popular?")** respondida
  com dados: fonte confirmada (URLs de origem em 20+ subreddits diversos); o viés é do nosso
  próprio funil — no ciclo 3, 71 das 76 rejeições de curadoria foram texto embutido/colagem
  (prints, manchetes, placares dominam o r/popular), e o gate de motion_potential (rosto em
  close) enriquece animais ainda mais. Viés documentado e ajustável, não um bug de fonte.
- [ ] Render dos 4 aprovados em andamento (textos encurtados em 2-3 palavras nos itens 1/2/4
      para ≤22 palavras/9,96s; specs de movimento por cena; câmera estática; item 4 é a
      aposta consciente de cena aberta com movimento vindo da água).
- **Novo modo de falha encontrado no render dos 4 aprovados**: "This cat keeps moving to
  always be in the sunshine" sofreu **drift catastrófico de cena no meio do clipe** — começa
  correto (gato no feixe de sol, frame t=1s confere com a imagem-base) e no meio vira um
  personagem humano 3D genérico olhando pro celular, sem relação nenhuma com a cena de
  origem. Áudio saiu junto quebrado (Whisper transcreveu algo sem relação com o texto
  pedido, só ~3s de fala de 9,96s). Distinto dos modos de falha já catalogados (imagem-base
  incoerente, ancoragem de tamanho/espécie frágil) — aqui a IMAGEM-BASE estava correta; o
  drift aconteceu na geração temporal do vídeo em si, violando a própria instrução do prompt
  ("no cuts, no scene transition"). Tratado como defeito de mídia-fonte/geração (não de
  texto): vídeo e imagem final apagados, imagem-base mantida (estava correta), 1 nova
  tentativa de render disparada.
- **Inspeção visual dos 4 revelou 3 de 4 com defeito real** (só o #4, piscina Star Wars, passou
  de primeira — áudio e cena corretos). #1 (gato bicolor): imagem-base não renderizou a
  divisão de cores, âncora inteira da piada ausente. #2 (gato no sol): drift catastrófico de
  cena pra um humano genérico — em AMBAS as tentativas (2/2), pior na segunda (drift já em
  t=1s) → **descartado**. #3 (bezerro highland): espécie trocada pra gato comum (mesmo padrão
  do caso do lobo). #1 e #3 na 2ª tentativa (resample completo da imagem-base) — ainda dentro
  do critério de 2 tentativas.
- **Causa raiz REAL do "prior de gato" encontrada (não era prior do modelo, era bug no
  código)**: `build_video_script` tinha um branch de fallback que dizia literalmente
  `"Character: One fictional cat or person already present..."` para qualquer sujeito não
  reconhecido pelas regex (bezerro, lobo, veado etc.) — o próprio prompt oferecia "cat" como
  alternativa, competindo com a menção real da espécie. E o branch de gato hardcodava "The
  orange cat" genérico, apagando detalhes específicos (perdeu a divisão de cores do gato
  bicolor). Confirmado lendo o `image_prompt` real usado no render do bezerro: "Character:
  One fictional cat or person...", igual ao código. **Corrigido**: os dois branches agora
  citam o `visual_summary` (a descrição real da cena) em vez de nomear "cat" como opção;
  nunca perdem a espécie/cor/marcas específicas. 2 testes de regressão novos (bezerro nunca
  menciona gato; gato bicolor preserva a heterocromia). Suíte: 37 passed.
- **Saldo do lote de 4 do ciclo 3**: 1/4 sobreviveu (piscina Star Wars, verificado e
  entregue), 3/4 descartados após 2 tentativas cada — mas a causa raiz dos 3 descartes é o
  bug agora corrigido, então o próximo lote deve ter uma taxa de sobrevivência bem mais alta
  nos casos não-gato/não-humano.
- [x] **Regra "bug corrigido zera tentativas" aplicada e validada — 4/4 entregues no fim.**
      Critério novo salvo em `joke-fix-retry-limit`. Resultado após zerar: gato no sol
      corrigiu de primeira (sem o drift catastrófico); gato bicolor e bezerro precisaram de
      mais uma rodada com o traço específico (heterocromia / espécie exata) injetado direto
      no `character` do video_script, bypassando a descrição visual do modelo de visão que
      era fraca demais nesses detalhes específicos. **Lição adicional**: nem todo problema de
      espécie/característica é o bug de código — quando a descrição visual upstream não
      captura um traço sutil (divisão de cor simétrica, raça específica), a solução é
      injetar o traço explicitamente no prompt em vez de confiar só no `visual_summary`.
      Lote final: 4/4 conceitos do ciclo 3 entregues (piscina, gato no sol, gato bicolor,
      bezerro highland).

## Pivô de arquitetura: "foto real narrada" (2026-07-18)

Após o veredito "não mudou nada, está muito ruim" nos 4 vídeos do ciclo 3, o usuário mandou
rever o plano inteiro. Plano aprovado (`~/.claude/plans/linear-wiggling-swing.md`) com
diagnóstico estrutural: re-gerar a imagem destrói o ativo (foto real), o áudio nativo do LTX
erra PT-BR sempre, áudio+vídeo acoplados encarecem cada iteração de texto, e a calibração de
duração era loteria. Stress-test por agente achou que ~70% da nova arquitetura já existia no
código (input-mode source, stack TTS/mux legado, zoompan).

Implementado em 4 commits:
1. **Full-res + gate de resolução**: preview.redd.it→i.redd.it (gato bicolor: 140px→1536×2048
   confirmado ao vivo); curadoria reprova lado menor <640px.
2. **TTS local plugável (Piper default)**: pronúncia de "salão" correta (a palavra que o LTX
   sempre errava); respelling de estrangeirismos; `frames_for_narration` deriva a duração do
   vídeo do áudio MEDIDO — fim da loteria palavras→duração.
3. **Engine photomotion (Tier 1)**: foto real, cortes SECOS por frase (nunca Ken Burns
   contínuo), legendas por frase, narração com delays medidos. CPU, segundos por vídeo.
4. **ltx23 audio-mode tts (Tier 2)**: I2V da foto real só-vídeo, prompt sem voice-over,
   trilha substituída pela narração medida no mux.

Validação em curso: Fase A (photomotion nos 4 textos aprovados), Fase B (Tier 2 nos mesmos),
Fase C (página HTML foto|Tier1|Tier2 para o veredito único que fecha o tier default e a voz).
- [x] **Validação Fases A e B concluídas e entregues.** Fase A (photomotion): 4/4 em minutos
      de CPU, narração verbatim — "salão", "lançamento", "energia solar" corretos (as
      palavras que o áudio nativo sempre quebrou). Fase B (Tier 2, I2V da foto real + mux
      TTS): 3/4 passaram na inspeção — o gato quimera REAL com divisão de cor e heterocromia
      preservadas (a âncora que a re-geração destruiu 3x), o bezerro highland REAL se
      movendo, a piscina real com câmera sobre as naves. Derivação de frames do áudio medido
      funcionou ao vivo ("narration: 8.96s measured → 225 frames"). 1/4 falhou: gato-no-sol
      driftou pela 3ª vez pro MESMO tipo de cena (homem com celular) — **padrão novo mapeado:
      fonte retrato extremo (640×1137) espremida em render paisagem 768×448 quebra a
      ancoragem do I2V; mitigação futura: casar a orientação do render com a da fonte** —
      fallback Tier 1 usado por design. Aguardando veredito do usuário: tier default, voz
      (piper vs edge), aprovação dos memes.
- [x] **VEREDITO DO USUÁRIO: "ok para os 3" — pivô validado nos 3 eixos** (piada, voz Piper,
      movimento) nos 3 vídeos Tier 2. Também definiu: "vídeos ok, fotos em movimento não" —
      Tier 2 é o formato ÚNICO de entrega; photomotion rebaixado a prévia interna. Os 3
      vídeos aprovados viraram baseline (`reference-baseline/APPROVED-pivot-*.mp4`).
      **Defaults do pipeline atualizados para a receita validada**: `--ltx23-input-mode
      source` + `--ltx23-audio-mode tts` + `--tts-backend piper` são agora o caminho padrão.
      Fix de orientação (fonte retrato → render retrato) commitado; retry do gato-no-sol em
      andamento.
- [x] **Escritor de piadas movido para modelo local free** (pedido do usuário: "texto da
      piada deveria ser um modelo eficiente porém free"). Bench com few-shot dos 6 exemplares
      aprovados em 3 posts curados: **gemma4:31b venceu com 3/3 no estilo narrador** ("tá
      conferindo se ele não roubou o café!"), gemma3:12b mediano, qwen3:14b continua ruim.
      Default `--humor-model` atualizado qwen3:8b→gemma4:31b. Fluxo: modelo local escreve →
      Claude revisa (barato) → veredito de texto do usuário → render.
- [x] **Gato-no-sol descartado em definitivo** após 4ª ocorrência do MESMO drift (homem de
      moletom com celular), inclusive em render retrato — orientação não era a causa. Lição
      nova: **sujeito ocupando fração mínima de um quadro vazio é âncora fraca para I2V**
      (gato pequeno num tapete vazio); candidato a critério futuro no gate de fonte
      (dominância do sujeito). O fix de orientação permanece (correto por si; retrato
      448×768 renderiza sem problema de memória).

## Lane de notícias (2026-07-18/19)

Nova direção do usuário: "criar memes virais com base nas notícias mais populares". O pivô
destravou isso — com narrador, a notícia é CONTADA pela voz (o gate de texto-embutido existia
porque o pipeline antigo não tinha como transmitir a história). Decisões do usuário: figuras
públicas PODEM ser animadas (guardrail mantido por design: voz sempre do narrador, nunca
lip-sync atribuindo fala à pessoa; movimento idle sutil); visual = foto da notícia quando
cena utilizável, senão foto de reação do acervo.

Primeiro ciclo completo do fluxo novo (custo de tokens ~zero até o veredito):
- Curadoria: 3 aprovados de 87 (gates novos bem mais rígidos).
- **gemma4:31b como escritor: 3/3 de consenso no funil animal** (vs ~10% histórico) e 6
  piadas de notícia decentes direto do título (1 descartada por sensibilidade — boato de
  morte). Usuário aprovou 8/8 textos.
- Render em lote de 8 na receita validada: 7/8 de primeira. Achados: teto de frames antigo
  (257) era do envelope de resolução maior — 273/281 frames passam em 768×448; placar de
  transmissão vira texto borrado na re-geração (ressalva entregue; melhoria futura: crop de
  UI); 1 falha de contrato ("missing observable action" em script do funil) corrigida com
  timeline de ação e re-render. Incidente operacional: pkill matou a própria shell (padrão
  casou com o comando) e deixou job órfão de 720×1280 na fila do ComfyUI (default de
  dimensões sem override + swap de retrato) — interrompido via API, relançado no envelope.
  Lição: sempre passar --ltx23-width/height explícitos e nunca pkill com padrão que casa a
  própria invocação.
- [x] **Ciclo fechado em 7/8 entregues.** O gato-na-hortelã foi descartado após a 6ª
      ocorrência do MESMO drift entre 2 conceitos distintos: **"gato deitado/dormindo em
      ambiente doméstico" é um atrator determinístico do LTX distilled — a cena teleporta
      para "homem de moletom mexendo no celular num quarto"** (2x live-action, 1x versão
      3D/Pixar), independente de seed, orientação e duração. A hipótese anterior
      (retrato→paisagem) foi refinada: orientação era fator secundário; o atrator é o
      conteúdo da cena. Critério novo de seleção: cena de animal dormindo parado em interior
      doméstico = alto risco de drift; preferir cenas com ação intrínseca ou sujeito em
      close dominante.
- [x] **VEREDITO: os 7 aprovados.** Primeiro lote em escala da arquitetura nova com aprovação
      integral — incluindo a lane de notícias completa (figura pública real animada aprovada:
      Deschamps vira exemplar no baseline). Placar do ciclo: 8/8 textos aprovados, 7/8
      renders aprovados, 1 descarte com achado (atrator de drift). O fluxo
      curadoria→texto-free→veredito→render→verificação está operacional de ponta a ponta.
14. **Pacote de publicação Fase 1 implementado** (`docs/superpowers/specs/2026-07-19-publish-package-phase1-design.md`): novo contrato v3 do `concepts.json` com seção `publish` (compatível com leitura v2 legado) gerada por modelo local configurável, até 3 tentativas com validação determinística — título ≤100 caracteres, 3–5 tópicos de interesse, 4–8 hashtags, português brasileiro. Cada vídeo aprovado recebe diretório `NN-slug/` com `publish.json` (metadados estruturados), `publish.txt` (caption colável em 1024 caracteres para Telegram), e `final_916.mp4` (resolução 1080×1920, padding com desfoque para conteúdo retrato via ffmpeg pós-validação — o MP4 nativo permanece intacto). Telegram (`--telegram`) integrado com envio de vídeo e caption; curadoria de r/popular prioriza fotos retrato no backlog (prioridade branda, paisagem permanece elegível). Fases 2–3 (métricas de engajamento e loop de feedback) não iniciadas.

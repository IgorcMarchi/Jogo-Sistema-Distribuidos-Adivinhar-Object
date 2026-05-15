# Avaliação Prática 01 – RPC/RMI
## Jogo Multijogador de Adivinhação de Objetos

**Disciplina:** Sistemas Distribuídos e Tecnologias (CC5SDT)  
**Semestre:** 2026-1  
**Professor:** Rafael Keller Tesser  
**Linguagem:** Python 3  
**Biblioteca RPC:** RPyC (Remote Python Call)

---

## 1. Introdução à Biblioteca RPyC

### O que é RPyC?

RPyC é uma biblioteca Python que permite fazer chamadas remotas de forma simples. A ideia principal é que você pode chamar funções que estão rodando em outro computador (ou em outro processo) praticamente da mesma forma que chamaria funções locais. O nome significa "Remote Python Call", ou seja, chamada remota em Python.

Ela funciona seguindo o modelo RPC (Remote Procedure Call): um lado expõe funções e o outro lado pode chamá-las como se fossem locais. No RPyC, qualquer método que começa com `exposed_` fica disponível para clientes remotos chamarem.

Um diferencial importante do RPyC é o suporte à comunicação bidirecional: não só o cliente chama o servidor, mas o servidor também pode chamar funções que estão no cliente. Isso é chamado de "callback assíncrono", e foi exatamente o que usamos para notificar os jogadores em tempo real sem precisar de polling.

### Por que escolhemos o RPyC?

Consideramos três opções principais: XML-RPC (que já vem no Python), gRPC (do Google) e RPyC.

- **XML-RPC** é simples mas bem limitado: não tem suporte nativo a callbacks e os tipos de dados que podem ser enviados são bem restritos.
- **gRPC** é muito poderoso e eficiente, mas exige criar arquivos `.proto` (definição de interface) e gerar código a partir deles. Para o tamanho do nosso projeto, isso seria trabalhoso demais.
- **RPyC** é direto ao ponto: você escreve a classe, coloca `exposed_` nos métodos e já está funcionando. Além disso, tem suporte nativo a notificações push (o servidor avisa os clientes), o que era fundamental para o nosso jogo.

A principal razão da escolha foi o suporte a callbacks assíncronos. Com RPyC e `rpyc.async_()`, o servidor consegue notificar cada cliente sem bloquear, evitando polling.

---

## 2. Descrição do Desenvolvimento

### 2.1 Arquitetura Geral

A aplicação segue o modelo **cliente-servidor**: todos os clientes se conectam a um único servidor central. O servidor mantém todo o estado do jogo (jogadores, pontuação, turnos, histórico de dicas, etc.) e os clientes enviam comandos e recebem notificações.

```
[Cliente A — GUI] ────────────────────────────────
                                                    \
[Cliente B — GUI] ─────────────── [SERVIDOR]
                                                    /
[Cliente C — GUI] ────────────────────────────────
```

Centralizar o estado no servidor é mais simples e garante consistência: não há como dois clientes terem versões diferentes do placar ou da ordem de turnos.

### 2.2 O Servidor (`servidor.py`)

O servidor é implementado como uma classe que herda de `rpyc.Service`. Os métodos com prefixo `exposed_` são os que os clientes podem chamar remotamente.

```python
import rpyc
from rpyc.utils.server import ThreadedServer

class GameServer(rpyc.Service):

    def exposed_entrar(self, nome: str) -> str:
        # cliente chama isso para entrar no lobby
        ...

    def exposed_enviar_dica(self, dica: str) -> str:
        # cliente chama isso para enviar uma dica no seu turno
        ...
```

O `ThreadedServer` cria uma thread separada por conexão, permitindo que vários jogadores se conectem simultaneamente sem bloquear uns aos outros.

**Estado global compartilhado:**

No RPyC cada conexão cria uma instância separada do `GameServer`, então o estado do jogo é guardado em variáveis de classe (compartilhadas entre todas as instâncias):

```python
class GameServer(rpyc.Service):
    _jogadores     = {}             # todos os jogadores conectados
    _turno_atual   = None           # quem está jogando agora
    _jogo_iniciado = False          # se a partida já começou
    _lock          = threading.Lock()
```

Para evitar condições de corrida, todo acesso a essas variáveis é feito dentro de `with GameServer._lock:`.

**Notificações em tempo real (callbacks):**

Quando o servidor precisa notificar todos os jogadores, usa `rpyc.async_()` para não bloquear enquanto espera cada cliente responder:

```python
def _broadcast_sistema(self, mensagem: str):
    for nome, info in list(GameServer._jogadores.items()):
        try:
            rpyc.async_(info["conn"].root.notificar_sistema)(mensagem)
        except Exception:
            pass
```

### 2.3 O Cliente (`cliente.py`)

O cliente possui uma **interface gráfica** construída com CustomTkinter, dividida em três colunas:

| Coluna | Conteúdo |
|---|---|
| Esquerda | Menu de ações (botões) |
| Centro | Objeto secreto do jogador (imagem PNG) |
| Direita | Notificações em tempo real + Chat |

O cliente também implementa um `rpyc.Service` (`ClienteService`) para receber callbacks do servidor. Para evitar problemas de thread-safety com Tkinter, os callbacks **nunca tocam nos widgets diretamente** — eles colocam eventos em uma `queue.Queue`, e o loop principal do Tkinter consome essa fila a cada 120 ms:

```python
class ClienteService(rpyc.Service):
    _fila: queue.Queue = None

    def exposed_notificar_sistema(self, mensagem: str):
        if ClienteService._fila:
            ClienteService._fila.put(("sistema", str(mensagem)))

    def exposed_receber_dica(self, remetente: str, dica: str):
        if ClienteService._fila:
            ClienteService._fila.put(("dica", str(remetente), str(dica)))
```

Uma thread daemon separada processa os callbacks RPyC continuamente:

```python
def _serve_loop(self):
    while True:
        try:
            self._conn.serve(0.1)
        except EOFError:
            break
```

**Exibição de objetos com imagens reais:**

Ao receber o objeto secreto, o cliente carrega a imagem PNG correspondente da pasta `objetos/` usando Pillow e a exibe no painel central. A imagem se redimensiona automaticamente quando a janela é redimensionada:

```python
def _set_objeto(self, nome_obj: str, arte: str = ""):
    caminho = os.path.join(_PASTA_OBJETOS, f"{nome_obj.lower()}.png")
    self._obj_pil = Image.open(caminho).convert("RGBA")
    self._redimensionar_imagem()
```

### 2.4 Funcionalidades Implementadas

#### Lobby e início da partida

O primeiro jogador a conectar se torna o **anfitrião**. Os demais ficam na tela de lobby aguardando. Só o anfitrião pode iniciar a partida.

Ao iniciar, o servidor sorteia um objeto secreto diferente para cada jogador (15 objetos disponíveis: espada, escudo, poção, mapa, chave, lanterna, corda, bússola, cristal, pergaminho, amuleto, tocha, cajado, elmo, flecha). A imagem do objeto aparece imediatamente no painel central do cliente.

#### Sistema de turnos

A cada turno o índice avança na lista de jogadores em rodízio circular. O limite de turnos por rodada é `número de jogadores × 3`. Ao atingir o limite, uma votação é aberta automaticamente.

#### Envio de dicas

Só o jogador da vez pode enviar dica — deve ser uma única palavra. Após enviar, o turno avança automaticamente e todos os outros jogadores são notificados via callback imediato.

#### Troca privada de dicas

Qualquer jogador pode solicitar uma troca privada com outro. O fluxo é:

1. Jogador A solicita troca com B informando sua palavra
2. O servidor notifica B via callback
3. B aceita (informando sua palavra) ou recusa
4. Se aceitar, cada um recebe a palavra do outro em privado
5. No início do próximo ciclo de turnos, **todos os jogadores são informados anonimamente** que dois jogadores realizaram uma troca — sem revelar quem foram nem o conteúdo

As palavras trocadas podem ser mentira. Cada jogador pode fazer até 3 trocas por rodada.

#### Espionagem

Qualquer jogador pode registrar uma espionagem sobre a troca entre outros dois jogadores específicos. Se a troca ocorrer, o espião recebe o conteúdo completo no início do seu próximo turno.

Se o espião for descoberto por um dos jogadores espiados (usando "Denunciar Espião"):
- O espião perde 2 pontos
- Quem o descobriu ganha **4 pontos**
- O turno avança automaticamente após a denúncia

#### Sistema de palpites

A qualquer momento, um jogador pode tentar adivinhar o objeto de outro. Independentemente de acertar ou errar, **o turno avança automaticamente** após o palpite.

O servidor compara o palpite com o objeto real (case-insensitive):

```python
acertou = chute.strip().lower() == objeto_real.lower()
```

#### Sistema de pontuação

| Situação | Pontos |
|---|---|
| Primeiro a adivinhar um objeto | +5 |
| Adivinhar depois de outro | +3 |
| Bônus: único a adivinhar (calculado ao encerrar) | +2 |
| Dono: apenas 1 jogador adivinhou seu objeto | +2 |
| Dono: todos os outros acertaram (dica fácil demais) | -1 |
| Ser pego espiando | -2 |
| Descobrir um espião | +4 |

#### Chat em tempo real

O chat possui duas modalidades:

- **Público**: mensagem enviada a todos imediatamente via callback
- **Privado**: mensagem enviada apenas ao destinatário (o espião, se houver, também recebe)

O chat é independente das mecânicas do jogo — enviar mensagem não consome turno.

#### Votação ao final da rodada

Ao atingir o limite de turnos, uma janela de votação abre automaticamente para todos. Cada jogador vota em **Continuar** ou **Encerrar**:

- **Maioria Continuar**: nova rodada começa com novos objetos sorteados para todos os jogadores
- **Maioria Encerrar**: placar final é exibido e o jogo termina

---

## 3. Instruções de Instalação e Uso

### 3.1 Pré-requisitos

- Python 3.8 ou superior
- Pasta `objetos/` com os 15 arquivos PNG dos objetos (já incluída no repositório)

### 3.2 Instalação das dependências

```bash
pip install rpyc customtkinter pillow
```

Se preferir usar ambiente virtual (recomendado):

```bash
# Criar o ambiente
python -m venv venv

# Ativar (Windows)
venv\Scripts\activate

# Ativar (Linux/Mac)
source venv/bin/activate

# Instalar dependências
pip install rpyc customtkinter pillow
```

### 3.3 Iniciando o Servidor

1. Abra um terminal na pasta do projeto
2. Execute:

```bash
python servidor.py
```

A saída esperada:
```
=======================================================
  Servidor do Jogo Multijogador — RPyC
  Porta: 18861
=======================================================
```

O servidor escuta em `127.0.0.1` na porta `18861`. Mantenha este terminal aberto durante toda a partida.

> **Atenção:** o servidor deve ser iniciado antes de qualquer cliente.

### 3.4 Conectando os Clientes

Para cada jogador, abra uma janela separada e execute:

```bash
python cliente.py
```

A janela gráfica abrirá pedindo o nome do jogador. São necessários pelo menos **2 jogadores** para iniciar a partida.

### 3.5 Como Jogar — Passo a Passo

**1. Lobby**
- O primeiro jogador a conectar é o anfitrião (marcado com 👑)
- Os demais entram e aguardam na tela de lobby
- O anfitrião clica em **Iniciar Partida** para começar

**2. Início da partida**
- Cada jogador recebe um objeto secreto diferente
- A imagem do objeto aparece no painel central ("Meu Objeto Secreto")
- O sistema define quem joga primeiro

**3. A cada turno**
- O jogador da vez envia uma dica clicando em **Enviar Dica Pública** — apenas uma palavra
- Todos os outros jogadores recebem a dica instantaneamente no painel de Notificações
- O turno passa automaticamente ao próximo jogador

**4. Troca privada de dicas**
- Clique em **Solicitar Troca Privada** e escolha um jogador
- Digite uma palavra (pode ser mentira)
- O outro jogador é notificado e pode **Aceitar / Recusar Troca**
- Se aceitar, cada um recebe a palavra do outro em privado
- No início do próximo ciclo, todos são informados anonimamente que houve uma troca

**5. Espionagem**
- Clique em **Espionar Troca** e escolha dois jogadores para espionar
- Se eles realizarem uma troca, você recebe o conteúdo no início do seu próximo turno
- Se for descoberto, use **Denunciar Espião** para punir o responsável

**6. Palpite**
- Clique em **Fazer Palpite** a qualquer momento
- Escolha o jogador alvo e digite o nome do objeto
- Acertando ou errando, o turno avança automaticamente

**7. Fim da rodada**
- Ao atingir o limite de turnos, uma janela de votação aparece para todos
- Cada um vota em **Continuar** ou **Encerrar**
- Se continuar: novos objetos são sorteados e uma nova rodada começa
- Se encerrar: o placar final é exibido

**8. Chat**
- **Mensagem pública**: campo na parte inferior direita → botão Enviar
- **Mensagem privada**: botão **Privado** ao lado do título Chat

**9. Ver informações**
- **Ver Placar**: exibe a pontuação atual de todos
- **Ver Jogadores**: exibe a lista de jogadores conectados na partida

**10. Sair**
- Feche a janela para sair do jogo

### 3.6 Rodando em Rede Local

Para jogar em computadores diferentes na mesma rede local, altere dois pontos:

No `servidor.py`, mude o `hostname` para aceitar conexões externas:

```python
servidor = ThreadedServer(
    GameServer,
    hostname="0.0.0.0",  # aceita conexões de qualquer IP
    port=18861,
    ...
)
```

No `cliente.py`, mude a constante `HOST` para o IP do servidor:

```python
HOST  = "192.168.X.X"   # IP do servidor na rede local
PORTA = 18861
```

---

## 4. Estrutura do Projeto

```
Jogo-Sistema-Distribuidos-Adivinhar-Object/
├── servidor.py          # Servidor central (lógica do jogo)
├── cliente.py           # Cliente com interface gráfica (CustomTkinter)
├── objetos/             # Imagens PNG dos 15 objetos secretos
│   ├── espada.png
│   ├── escudo.png
│   ├── poção.png
│   ├── mapa.png
│   ├── chave.png
│   ├── lanterna.png
│   ├── corda.png
│   ├── bússola.png
│   ├── cristal.png
│   ├── pergaminho.png
│   ├── amuleto.png
│   ├── tocha.png
│   ├── cajado.png
│   ├── elmo.png
│   └── flecha.png
└── README.md
```

---

## 5. Considerações Finais

A principal decisão técnica do projeto foi usar RPyC com callbacks assíncronos, evitando polling. Isso garante que notificações como "dica recebida", "troca solicitada" e "turno avançado" cheguem imediatamente a todos os jogadores.

A interface gráfica foi construída com CustomTkinter e usa Pillow para exibir as imagens reais dos objetos secretos no painel central do cliente. A imagem se redimensiona automaticamente conforme o tamanho da janela. A comunicação entre a thread RPyC (que recebe callbacks) e a thread principal do Tkinter é feita exclusivamente via `queue.Queue`, evitando condições de corrida na interface.

O estado centralizado no servidor garante consistência total: não há como dois clientes terem versões diferentes do placar, da ordem de turnos ou das pontuações, pois tudo está armazenado em um único lugar protegido por `threading.Lock()`.

# Docker Manager

Ferramenta moderna para gerenciar containers Docker com interface rica e interativa.

## Visão Geral

Suite de scripts que oferece interface amigável para gerenciar containers, imagens e redes Docker. Combina menus interativos com tabelas formatadas usando Rich.

## Uso

### Menu Interativo

```bash
./docker.sh
```

### Comandos Diretos

```bash
./docker.sh ps       # Lista containers em execução
./docker.sh images   # Lista imagens disponíveis
./docker.sh net      # Lista redes Docker
./docker.sh ports    # Visualiza portas expostas
./docker.sh watch    # Monitora containers continuamente
```

### Gerenciamento de Portas

O comando `ports` oferece uma visão funcional das portas expostas:

```bash
./docker.sh ports                    # Lista portas em tabela
./docker.sh ports --interactive      # Modo interativo completo
```

**Modo interativo inclui:**
- Seleção de containers por porta
- Detalhes completos do container
- Logs em tempo real
- Parar containers com confirmação

## Contribuição

Este projeto faz parte da suite linuxtools. Contribuições são bem-vindas via pull requests.

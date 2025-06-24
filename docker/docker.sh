#!/bin/bash

# Docker Manager - Wrapper script com menu interativo
# Localização do script Python - detecta se é um symlink e resolve o caminho real
if [ -L "$0" ]; then
    # Se for um symlink, segue para o arquivo real
    SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
else
    # Se executado diretamente
    SCRIPT_DIR="$(dirname "$(realpath "$0")")"
fi

PYTHON_SCRIPT="$SCRIPT_DIR/docker.py"

# Detecta o comando uv (primeiro tenta PATH, depois ~/.local/bin)
if command -v uv >/dev/null 2>&1; then
    UV_CMD="uv"
elif [ -f ~/.local/bin/uv ]; then
    UV_CMD="~/.local/bin/uv"
else
    echo "Erro: uv não encontrado. Instale com: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Se não há parâmetros, mostrar menu interativo
if [ $# -eq 0 ]; then
    echo "Docker Manager - Selecione uma opção:"
    choice=$(gum choose \
        "ps - Lista containers em execução" \
        "net - Lista redes Docker" \
        "images - Lista imagens Docker" \
        "watch - Monitora containers continuamente" \
        "Sair")
    
    case "$choice" in
        "ps - Lista containers em execução")
            param="ps"
            ;;
        "net - Lista redes Docker")
            param="net"
            ;;
        "images - Lista imagens Docker")
            param="images"
            ;;
        "watch - Monitora containers continuamente")
            param="watch"
            ;;
        "Sair"|*)
            echo "Saindo..."
            exit 0
            ;;
    esac
    
    # Executar o comando selecionado
    $UV_CMD run --with rich "$PYTHON_SCRIPT" "$param"
else
    # Passar todos os parâmetros para o script Python
    $UV_CMD run --with rich "$PYTHON_SCRIPT" "$@"
fi

#!/bin/bash

# LinuxTools - Script de instalação
# Instala dependências e cria links simbólicos para todas as ferramentas

set -e

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Funções de output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Função para perguntar sim/não
ask_yes_no() {
    while true; do
        read -p "$1 (y/n): " yn
        case $yn in
            [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            * ) echo "Por favor responda sim (y) ou não (n).";;
        esac
    done
}

# Detecta o sistema operacional
check_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        print_error "Sistema macOS: Instalação destinado apenas para servidores Ubuntu/Linux"
        exit 1
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Verifica se é Ubuntu/Debian
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
                print_warning "Sistema Linux: $PRETTY_NAME"
                if ! ask_yes_no "Deseja continuar mesmo assim?"; then
                    print_info "Instalação cancelada"
                    exit 0
                fi
            else
                print_success "Sistema Ubuntu/Debian: $PRETTY_NAME"
            fi
        else
            print_warning "Não foi possível detectar a distribuição Linux"
            if ! ask_yes_no "Deseja continuar mesmo assim?"; then
                print_info "Instalação cancelada"
                exit 0
            fi
        fi
    else
        print_error "Sistema operacional não suportado: $OSTYPE"
        print_info "Este script funciona apenas em servidores Ubuntu/Linux"
        exit 1
    fi
}

# Verifica se Docker está instalado e funcionando
check_docker() {
    print_info "Verificando Docker..."
    
    if ! command -v docker >/dev/null 2>&1; then
        print_error "Docker não está instalado"
        print_info "Instale o Docker primeiro:"
        print_info "curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh"
        exit 1
    fi
    
    print_success "Docker encontrado: $(docker --version)"
    
    # Verifica se o Docker está funcionando
    if ! docker info >/dev/null 2>&1; then
        print_error "Docker não está funcionando ou você não tem permissão"
        print_info "Verifique se:"
        print_info "1. O serviço Docker está rodando: sudo systemctl start docker"
        print_info "2. Seu usuário está no grupo docker: sudo usermod -aG docker \$USER"
        print_info "3. Faça logout/login novamente após adicionar ao grupo"
        exit 1
    fi
    
    print_success "Docker está funcionando corretamente"
    
    # Testa com um comando simples
    if docker run --rm hello-world >/dev/null 2>&1; then
        print_success "Docker testado com sucesso"
    else
        print_warning "Docker instalado mas teste falhou"
        print_info "Pode ser necessário configurar permissões ou reiniciar o serviço"
    fi
}

# Detecta o diretório do projeto
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Verifica se está executando como root para /usr/local/bin
check_sudo() {
    if [ "$EUID" -ne 0 ]; then
        print_error "Este script precisa ser executado com sudo para criar links em /usr/local/bin"
        print_info "Execute: sudo $0"
        exit 1
    fi
}


# Encontra todas as ferramentas disponíveis
find_tools() {
    local tools=()
    
    # Procura por diretórios que contêm scripts .sh
    for dir in "$PROJECT_DIR"/*/; do
        if [ -d "$dir" ]; then
            local tool_name=$(basename "$dir")
            local script_path="$dir${tool_name}.sh"
            
            if [ -f "$script_path" ] && [ -x "$script_path" ]; then
                tools+=("$tool_name:$script_path")
                # Envia mensagem para stderr para não interferir na captura
                print_info "Encontrada ferramenta: $tool_name" >&2
            fi
        fi
    done
    
    printf '%s\n' "${tools[@]}"
}

# Cria links simbólicos
create_links() {
    local tools=($(find_tools))
    
    if [ ${#tools[@]} -eq 0 ]; then
        print_warning "Nenhuma ferramenta encontrada para instalar"
        return 1
    fi
    
    print_info "Criando links simbólicos em /usr/local/bin/..."
    
    for tool_info in "${tools[@]}"; do
        IFS=':' read -r tool_name script_path <<< "$tool_info"
        local link_path="/usr/local/bin/${tool_name}.sh"
        
        # Remove link existente se houver
        if [ -L "$link_path" ] || [ -f "$link_path" ]; then
            print_warning "Removendo link/arquivo existente: $link_path"
            rm -f "$link_path"
        fi
        
        # Cria novo link simbólico
        ln -s "$script_path" "$link_path"
        print_success "Link criado: ${tool_name}.sh -> $script_path"
    done
}

# Função principal
main() {
    print_info "=== LinuxTools - Instalador ==="
    
    # Verifica sistema operacional
    check_os
    
    echo
    print_info "=== Verificando Docker ==="
    check_docker
    
    echo
    print_info "Este script irá:"
    print_info "1. Verificar e instalar dependências"
    print_info "2. Criar links simbólicos em /usr/local/bin/"
    echo
    
    if ! ask_yes_no "Deseja continuar?"; then
        print_info "Instalação cancelada"
        exit 0
    fi
    
    # Verifica se precisa de sudo
    check_sudo
    
    echo
    print_info "=== Verificando dependências ==="
    
    # Instala dependências (executa como usuário original, não root)
    sudo -u "$SUDO_USER" HOME="/home/$SUDO_USER" bash -c "
        # Re-define funções no contexto do usuário
        print_info() { echo -e \"\033[0;34m[INFO]\033[0m \$1\"; }
        print_success() { echo -e \"\033[0;32m[SUCCESS]\033[0m \$1\"; }
        print_warning() { echo -e \"\033[1;33m[WARNING]\033[0m \$1\"; }
        print_error() { echo -e \"\033[0;31m[ERROR]\033[0m \$1\"; }
        ask_yes_no() {
            while true; do
                read -p \"\$1 (y/n): \" yn
                case \$yn in
                    [Yy]* ) return 0;;
                    [Nn]* ) return 1;;
                    * ) echo \"Por favor responda sim (y) ou não (n).\";;
                esac
            done
        }
        
        # Verifica e instala uv
        install_uv() {
            # Verifica tanto no PATH quanto em ~/.local/bin
            if command -v uv >/dev/null 2>&1 || [ -f ~/.local/bin/uv ]; then
                if command -v uv >/dev/null 2>&1; then
                    print_success \"uv já está instalado: \$(uv --version)\"
                else
                    print_success \"uv encontrado em ~/.local/bin\"
                fi
                return 0
            fi
            
            print_warning \"uv não encontrado\"
            if ask_yes_no \"Deseja instalar o uv (gerenciador de pacotes Python)?\"; then
                print_info \"Instalando uv...\"
                curl -LsSf https://astral.sh/uv/install.sh | sh
                
                # Adiciona ao PATH se necessário
                export PATH=\"\$HOME/.local/bin:\$PATH\"
                if command -v uv >/dev/null 2>&1; then
                    print_success \"uv instalado com sucesso\"
                    print_info \"Adicione ~/.local/bin ao seu PATH permanentemente:\"
                    print_info \"echo 'export PATH=\\\"\\\$HOME/.local/bin:\\\$PATH\\\"' >> ~/.bashrc\"
                else
                    print_error \"Falha na instalação do uv\"
                    return 1
                fi
            else
                print_warning \"uv não será instalado. Algumas ferramentas podem não funcionar.\"
                return 1
            fi
        }
        
        # Verifica e instala gum
        install_gum() {
            if command -v gum >/dev/null 2>&1; then
                print_success \"gum já está instalado: \$(gum --version)\"
                return 0
            fi
            
            print_warning \"gum não encontrado\"
            if ask_yes_no \"Deseja instalar o gum (ferramenta para interfaces interativas)?\"; then
                print_info \"Instalando gum...\"
                
                # Instala gum no Ubuntu/Debian
                if command -v apt-get >/dev/null 2>&1; then
                    sudo mkdir -p /etc/apt/keyrings
                    curl -fsSL https://repo.charm.sh/apt/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg
                    echo \"deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *\" | sudo tee /etc/apt/sources.list.d/charm.list
                    sudo apt update && sudo apt install gum -y
                elif command -v yum >/dev/null 2>&1; then
                    echo '[charm]
name=Charm
baseurl=https://repo.charm.sh/yum/
enabled=1
gpgcheck=1
gpgkey=https://repo.charm.sh/yum/gpg.key' | sudo tee /etc/yum.repos.d/charm.repo
                    sudo yum install gum -y
                else
                    print_warning \"Gerenciador de pacotes não suportado\"
                    print_info \"Instale gum manualmente: https://github.com/charmbracelet/gum#installation\"
                    return 1
                fi
                
                if command -v gum >/dev/null 2>&1; then
                    print_success \"gum instalado com sucesso\"
                else
                    print_error \"Falha na instalação do gum\"
                    return 1
                fi
            else
                print_warning \"gum não será instalado. Menus interativos não funcionarão.\"
                return 1
            fi
        }
        
        # Executa as instalações
        install_uv
        install_gum
    "
    
    echo
    print_info "=== Instalando ferramentas ==="
    
    # Cria links simbólicos
    create_links
    
    echo
    print_success "=== Instalação concluída! ==="
    print_info "As ferramentas agora estão disponíveis globalmente:"
    
    local tools=($(find_tools))
    for tool_info in "${tools[@]}"; do
        IFS=':' read -r tool_name script_path <<< "$tool_info"
        print_info "  - ${tool_name}.sh"
    done
    
    echo
    print_info "Para usar, simplesmente digite o nome da ferramenta em qualquer terminal."
}

# Executa função principal
main "$@"
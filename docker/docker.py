# \!/usr/bin/env python3
# /// script
# dependencies = ["rich", "simple-term-menu"]
# ///

import re
import subprocess
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table
from simple_term_menu import TerminalMenu

console = Console()


def run_command(cmd):
    """Execute a shell command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            # Se comando falhou, pode ser problema de Docker
            if "docker" in cmd and "Cannot connect to the Docker daemon" in result.stderr:
                console.print("[red]Docker não está acessível[/red]")
                console.print("[yellow]Verifique se:[/yellow]")
                console.print("• Docker está instalado e rodando")
                console.print("• Seu usuário tem permissão (grupo docker)")
                console.print("• Serviço está ativo: sudo systemctl start docker")
                sys.exit(1)
            elif "docker" in cmd and result.stderr:
                console.print(f"[red]Erro Docker: {result.stderr.strip()}[/red]")
                sys.exit(1)
        return result.stdout.strip()
    except Exception as e:
        console.print(f"[red]Erro: {e}[/red]")
        return ""


def format_date(date_str):
    """Convert Docker date format to YYYY-MM-DD HH:MM:SS"""
    try:
        # Docker format: "2025-06-24 09:55:20 -0300 -03"
        # Remove timezone part and parse
        date_part = re.sub(r" -\d{4} -\d{2}$", "", date_str.strip())
        dt = datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return date_str  # Return original if parsing fails


def parse_ports(port_string):
    """Parse Docker port string and extract host port mappings, grouping protocols"""
    ports_dict = {}
    if not port_string or port_string.strip() == "":
        return []
    
    # Split multiple port mappings
    port_parts = port_string.split(", ")
    
    for part in port_parts:
        part = part.strip()
        # Match patterns like: 0.0.0.0:8080->80/tcp or [::]:8080->80/tcp
        match = re.search(r'(?:0\.0\.0\.0|::|\[::\]):(\d+)->', part)
        if match:
            host_port = int(match.group(1))
            # Extract container port and protocol
            container_match = re.search(r'->(\d+)/(\w+)', part)
            container_port = container_match.group(1) if container_match else "unknown"
            protocol = container_match.group(2) if container_match else "tcp"
            
            # Create unique key for host_port + container_port combination
            port_key = f"{host_port}->{container_port}"
            
            if port_key not in ports_dict:
                ports_dict[port_key] = {
                    'host_port': host_port,
                    'container_port': container_port,
                    'protocols': set(),
                    'mapping': part
                }
            
            ports_dict[port_key]['protocols'].add(protocol)
    
    # Convert to list and sort protocols
    ports = []
    for port_info in ports_dict.values():
        protocols_list = sorted(list(port_info['protocols']))
        ports.append({
            'host_port': port_info['host_port'],
            'container_port': port_info['container_port'],
            'protocol': ', '.join(protocols_list),
            'mapping': port_info['mapping']
        })
    
    return sorted(ports, key=lambda x: x['host_port'])


def create_ps_table():
    """Create table for docker ps"""
    table = Table(
        title="Docker Containers", show_header=True, header_style="bold magenta"
    )
    table.add_column("CONTAINER ID", style="cyan", no_wrap=True)
    table.add_column("IMAGE", style="white")
    table.add_column("CREATED", style="blue")
    table.add_column("STATUS", style="magenta")
    table.add_column("PORTS", style="yellow")

    output = run_command(
        'docker ps --format "{{.ID}}|{{.Image}}|{{.CreatedAt}}|{{.Status}}|{{.Ports}}"'
    )

    for line in output.split("\n"):
        if line.strip():
            parts = line.split("|")
            if len(parts) >= 5:
                created_formatted = format_date(parts[2])
                table.add_row(parts[0], parts[1], created_formatted, parts[3], parts[4])
            elif len(parts) == 4:
                created_formatted = format_date(parts[2])
                table.add_row(parts[0], parts[1], created_formatted, parts[3], "")

    return table


def create_ports_table():
    """Create table for exposed ports"""
    table = Table(
        title="Exposed Ports", show_header=True, header_style="bold magenta"
    )
    table.add_column("PORTS", style="yellow", no_wrap=True)
    table.add_column("PROTOCOL", style="blue", no_wrap=True)
    table.add_column("CONTAINER ID", style="cyan", no_wrap=True)
    table.add_column("CONTAINER NAME", style="green")
    table.add_column("IMAGE", style="white")
    table.add_column("STATUS", style="magenta")

    output = run_command(
        'docker ps --format "{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"'
    )

    # Collect all port mappings
    port_mappings = []
    
    for line in output.split("\n"):
        if line.strip():
            parts = line.split("|")
            if len(parts) >= 5:
                container_id = parts[0]
                container_name = parts[1]
                image = parts[2]
                status = parts[3]
                ports_str = parts[4]
                
                # Parse ports for this container
                ports = parse_ports(ports_str)
                
                for port_info in ports:
                    # Format ports column: show host port, add container port in parentheses if different
                    if str(port_info['host_port']) == port_info['container_port']:
                        ports_display = str(port_info['host_port'])
                    else:
                        ports_display = f"{port_info['host_port']} ({port_info['container_port']})"
                    
                    port_mappings.append({
                        'host_port': port_info['host_port'],
                        'ports_display': ports_display,
                        'protocol': port_info['protocol'],
                        'container_id': container_id[:12],
                        'container_name': container_name,
                        'image': image,
                        'status': status
                    })
    
    # Sort by host port
    port_mappings.sort(key=lambda x: x['host_port'])
    
    # Add rows to table
    for mapping in port_mappings:
        table.add_row(
            mapping['ports_display'],
            mapping['protocol'],
            mapping['container_id'],
            mapping['container_name'],
            mapping['image'],
            mapping['status']
        )
    
    return table, port_mappings


def create_network_table():
    """Create table for docker networks"""
    table = Table(
        title="Docker Networks", show_header=True, header_style="bold magenta"
    )
    table.add_column("NETWORK ID", style="cyan", no_wrap=True)
    table.add_column("NAME", style="green")
    table.add_column("DRIVER", style="yellow")
    table.add_column("SCOPE", style="blue")

    output = run_command(
        'docker network ls --format "{{.ID}}|{{.Name}}|{{.Driver}}|{{.Scope}}"'
    )

    for line in output.split("\n"):
        if line.strip():
            parts = line.split("|")
            if len(parts) >= 4:
                table.add_row(parts[0], parts[1], parts[2], parts[3])

    return table


def create_images_table():
    """Create table for docker images"""
    table = Table(title="Docker Images", show_header=True, header_style="bold magenta")
    table.add_column("REPOSITORY", style="white")
    table.add_column("TAG", style="green")
    table.add_column("IMAGE ID", style="cyan", no_wrap=True)
    table.add_column("CREATED", style="blue")
    table.add_column("SIZE", style="yellow")

    output = run_command(
        'docker images --format "{{.Repository}}|{{.Tag}}|{{.ID}}|{{.CreatedAt}}|{{.Size}}"'
    )

    for line in output.split("\n"):
        if line.strip():
            parts = line.split("|")
            if len(parts) >= 5:
                created_formatted = format_date(parts[3])
                table.add_row(parts[0], parts[1], parts[2], created_formatted, parts[4])

    return table


def watch_containers():
    """Continuously monitor containers"""
    try:
        with Live(console=console, refresh_per_second=1) as live:
            while True:
                table = create_ps_table()
                live.update(table)
                time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor interrompido[/yellow]")


def get_container_details(container_id):
    """Get detailed information about a container"""
    try:
        # Get container inspect data
        inspect_output = run_command(f"docker inspect {container_id}")
        
        # Get basic info with docker ps
        ps_output = run_command(
            f'docker ps --filter "id={container_id}" --format "{{{{.ID}}}}|{{{{.Names}}}}|{{{{.Image}}}}|{{{{.Status}}}}|{{{{.CreatedAt}}}}|{{{{.Ports}}}}"'
        )
        
        if not ps_output:
            return None
            
        parts = ps_output.split("|")
        if len(parts) < 5:
            return None
            
        details = {
            'id': parts[0],
            'name': parts[1],
            'image': parts[2],
            'status': parts[3],
            'created': format_date(parts[4]) if len(parts) > 4 else "Unknown",
            'ports': parts[5] if len(parts) > 5 else "None"
        }
        
        # Get network info
        network_output = run_command(
            f'docker inspect {container_id} --format "{{{{range .NetworkSettings.Networks}}}}{{{{.NetworkMode}}}} {{{{.IPAddress}}}} {{{{end}}}}"'
        )
        details['networks'] = network_output.strip() if network_output else "Unknown"
        
        # Get mounts/volumes
        mounts_output = run_command(
            f'docker inspect {container_id} --format "{{{{range .Mounts}}}}{{{{.Source}}}}:{{{{.Destination}}}} {{{{end}}}}"'
        )
        details['mounts'] = mounts_output.strip() if mounts_output else "None"
        
        return details
        
    except Exception as e:
        console.print(f"[red]Erro ao obter detalhes: {e}[/red]")
        return None


def show_container_logs(container_id):
    """Show container logs with real-time following"""
    try:
        console.print(f"[green]Logs do container {container_id[:12]}...[/green]")
        console.print("[dim]Pressione Ctrl+C para parar[/dim]\n")
        
        # Use subprocess with real-time output
        process = subprocess.Popen(
            ["docker", "logs", "--tail", "50", "--follow", container_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        try:
            for line in iter(process.stdout.readline, ''):
                if line:
                    print(line.rstrip())
        except KeyboardInterrupt:
            process.terminate()
            console.print("\n[yellow]Logs interrompidos[/yellow]")
        finally:
            process.terminate()
            
    except Exception as e:
        console.print(f"[red]Erro ao exibir logs: {e}[/red]")


def stop_container_interactive(container_id, container_name):
    """Stop container with confirmation using simple-term-menu"""
    try:
        # Use simple-term-menu for confirmation
        choices = ["Sim, parar container", "Cancelar"]
        
        terminal_menu = TerminalMenu(
            choices,
            title=f"Parar container {container_name} ({container_id[:12]})?",
            menu_cursor="=> ",
            menu_cursor_style=("fg_red", "bold"),
            menu_highlight_style=("bg_red", "fg_white"),
            cycle_cursor=True,
            clear_screen=False
        )
        
        menu_entry_index = terminal_menu.show()
        
        # Handle cancellation (Esc or Ctrl+C) or "Cancelar" selection
        if menu_entry_index is None or menu_entry_index == 1:
            console.print("[blue]Operação cancelada[/blue]")
            return False
        
        # User confirmed (index 0)
        if menu_entry_index == 0:
            console.print(f"[yellow]Parando container {container_name}...[/yellow]")
            stop_output = run_command(f"docker stop {container_id}")
            console.print(f"[green]Container {container_name} parado com sucesso[/green]")
            return True
            
    except Exception as e:
        console.print(f"[red]Erro ao parar container: {e}[/red]")
        return False


def logs_interactive_mode():
    """Interactive mode for container logs selection"""
    try:
        # Get containers table
        table = create_ps_table()
        
        # Get container data
        output = run_command(
            'docker ps --format "{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}"'
        )
        
        containers = []
        for line in output.split("\n"):
            if line.strip():
                parts = line.split("|")
                if len(parts) >= 4:
                    containers.append({
                        'id': parts[0],
                        'name': parts[1],
                        'image': parts[2],
                        'status': parts[3]
                    })
        
        if not containers:
            console.print("[yellow]Nenhum container em execução encontrado[/yellow]")
            return
        
        # Show containers table
        console.print(table)
        console.print()
        
        # Create choices for simple-term-menu
        choices = []
        for container in containers:
            choice = f"{container['name']} ({container['id'][:12]}) - {container['image']}"
            choices.append(choice)
        
        choices.append("Voltar ao menu principal")
        
        # Use simple-term-menu for selection
        terminal_menu = TerminalMenu(
            choices,
            title="Selecione um container para logs:",
            menu_cursor="=> ",
            menu_cursor_style=("fg_green", "bold"),
            menu_highlight_style=("bg_green", "fg_black"),
            cycle_cursor=True,
            clear_screen=False
        )
        
        menu_entry_index = terminal_menu.show()
        
        # Handle cancellation (Esc or Ctrl+C)
        if menu_entry_index is None:
            return
            
        selected_choice = choices[menu_entry_index]
        
        if selected_choice == "Voltar ao menu principal":
            return
        
        # Find selected container by index
        if menu_entry_index < len(containers):
            selected_container = containers[menu_entry_index]
            show_container_logs(selected_container['id'])
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Seleção de logs cancelada[/yellow]")
    except Exception as e:
        console.print(f"[red]Erro na seleção de logs: {e}[/red]")


def ports_interactive_mode():
    """Interactive mode for port management"""
    try:
        while True:
            # Get ports table and data
            ports_table, port_mappings = create_ports_table()
            
            if not port_mappings:
                console.print("[yellow]Nenhuma porta exposta encontrada[/yellow]")
                return
            
            # Show ports table
            console.print(ports_table)
            console.print()
            
            # Create choices for simple-term-menu
            choices = []
            for mapping in port_mappings:
                choice = f":{mapping['ports_display']} -> {mapping['container_name']} ({mapping['container_id']})"
                choices.append(choice)
            
            choices.append("Voltar ao menu principal")
            
            # Use simple-term-menu for selection
            terminal_menu = TerminalMenu(
                choices,
                title="Selecione um container:",
                menu_cursor="=> ",
                menu_cursor_style=("fg_red", "bold"),
                menu_highlight_style=("bg_red", "fg_yellow"),
                cycle_cursor=True,
                clear_screen=False
            )
            
            menu_entry_index = terminal_menu.show()
            
            # Handle cancellation (Esc or Ctrl+C)
            if menu_entry_index is None:
                break
                
            selected_choice = choices[menu_entry_index]
            
            if selected_choice == "Voltar ao menu principal":
                break
            
            # Find selected container by index
            if menu_entry_index < len(port_mappings):
                selected_container = port_mappings[menu_entry_index]
                # Container actions menu
                container_menu(selected_container)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Modo interativo cancelado[/yellow]")
    except Exception as e:
        console.print(f"[red]Erro no modo interativo: {e}[/red]")


def container_menu(container_info):
    """Show menu for container actions"""
    try:
        container_id = container_info['container_id']
        container_name = container_info['container_name']
        
        while True:
            console.print(f"\n[bold cyan]Container: {container_name} ({container_id})[/bold cyan]")
            
            # Action choices
            actions = [
                "Ver detalhes completos",
                "Acompanhar logs",
                "Parar container",
                "Voltar à lista de portas"
            ]
            
            # Use simple-term-menu for container actions
            terminal_menu = TerminalMenu(
                actions,
                title="Escolha uma ação:",
                menu_cursor="=> ",
                menu_cursor_style=("fg_blue", "bold"),
                menu_highlight_style=("bg_blue", "fg_white"),
                cycle_cursor=True,
                clear_screen=False
            )
            
            menu_entry_index = terminal_menu.show()
            
            # Handle cancellation (Esc or Ctrl+C)
            if menu_entry_index is None:
                break
                
            selected_action = actions[menu_entry_index]
            
            if selected_action == "Ver detalhes completos":
                show_container_details_full(container_id)
                
            elif selected_action == "Acompanhar logs":
                show_container_logs(container_id)
                
            elif selected_action == "Parar container":
                stopped = stop_container_interactive(container_id, container_name)
                if stopped:
                    console.print("[green]Voltando ao menu principal...[/green]")
                    return  # Return to main menu since container is stopped
                    
            elif selected_action == "Voltar à lista de portas":
                break
                
    except KeyboardInterrupt:
        console.print("\n[yellow]Menu do container cancelado[/yellow]")
    except Exception as e:
        console.print(f"[red]Erro no menu do container: {e}[/red]")


def show_container_details_full(container_id):
    """Display full container details"""
    details = get_container_details(container_id)
    
    if not details:
        console.print("[red]Não foi possível obter detalhes do container[/red]")
        return
    
    console.print(f"\n[bold green]Detalhes do Container[/bold green]")
    console.print(f"[cyan]ID:[/cyan] {details['id']}")
    console.print(f"[cyan]Nome:[/cyan] {details['name']}")
    console.print(f"[cyan]Imagem:[/cyan] {details['image']}")
    console.print(f"[cyan]Status:[/cyan] {details['status']}")
    console.print(f"[cyan]Criado:[/cyan] {details['created']}")
    console.print(f"[cyan]Portas:[/cyan] {details['ports']}")
    console.print(f"[cyan]Redes:[/cyan] {details['networks']}")
    console.print(f"[cyan]Volumes:[/cyan] {details['mounts']}")
    
    console.print("\n[dim]Pressione Enter para continuar...[/dim]")
    input()


def check_docker():
    """Check if Docker is accessible"""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if result.returncode != 0:
            console.print("[red]Docker não está acessível[/red]")
            if "Cannot connect to the Docker daemon" in result.stderr:
                console.print("[yellow]Soluções:[/yellow]")
                console.print("• Instalar Docker: curl -fsSL https://get.docker.com | sh")
                console.print("• Iniciar serviço: sudo systemctl start docker")
                console.print("• Adicionar usuário ao grupo: sudo usermod -aG docker $USER")
                console.print("• Fazer logout/login após adicionar ao grupo")
            else:
                console.print(f"[red]{result.stderr.strip()}[/red]")
            sys.exit(1)
    except FileNotFoundError:
        console.print("[red]Docker não está instalado[/red]")
        console.print("[yellow]Instale com:[/yellow] curl -fsSL https://get.docker.com | sh")
        sys.exit(1)

def show_help():
    """Show help message"""
    console.print("[bold green]Docker Manager[/bold green]")
    console.print("")
    console.print("[cyan]Comandos:[/cyan]")
    console.print("  [bold]ps[/bold]              Lista containers")
    console.print("  [bold]net, network[/bold]    Lista redes")
    console.print("  [bold]images[/bold]          Lista imagens")
    console.print("  [bold]ports[/bold]           Visualiza portas expostas")
    console.print("  [bold]logs[/bold]            Acompanha logs de containers")
    console.print("  [bold]watch[/bold]           Monitor contínuo")
    console.print("")
    console.print("[dim]Use docker.sh para menu interativo[/dim]")


def main():
    if len(sys.argv) == 1:
        show_help()
        return

    # Verifica Docker antes de executar comandos
    check_docker()

    command = sys.argv[1].lower()

    if command == "ps":
        table = create_ps_table()
        console.print(table)
    elif command in ["net", "network"]:
        table = create_network_table()
        console.print(table)
    elif command == "images":
        table = create_images_table()
        console.print(table)
    elif command == "ports":
        # Always activate interactive mode for ports command
        ports_interactive_mode()
    elif command == "logs":
        # Interactive container logs selection
        logs_interactive_mode()
    elif command == "watch":
        console.print("[green]Monitor iniciado[/green] [dim](Ctrl+C para parar)[/dim]")
        watch_containers()
    else:
        show_help()


if __name__ == "__main__":
    main()

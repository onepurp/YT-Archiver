#!/bin/bash

# ==============================================================================
# YT-Archiver Definitive Setup Script (v3.1 - PPA Support)
# ==============================================================================
# This script provides a comprehensive, one-click setup for YT-Archiver on Linux.
# It intelligently handles:
#   - System checks (OS, Python version)
#   - Automatic dependency installation (with user consent)
#   - PPA addition for newer Python on Debian/Ubuntu if needed
#   - Rclone configuration
#   - Repository cloning and Python environment setup
#   - Interactive application configuration
#   - Generation of a robust systemd service for background operation
# ==============================================================================

# --- Configuration ---
REPO_URL="https://github.com/onepurp/YT-Archiver"
REPO_DIR="YT-Archiver"
MIN_PYTHON_VERSION="3.8"
PYTHON_CMD="python3" # Default Python command, may be updated by check_system

# --- Colors for better output ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Helper Functions ---
print_info() { echo -e "${BLUE}INFO: $1${NC}"; }
print_success() { echo -e "${GREEN}SUCCESS: $1${NC}"; }
print_warning() { echo -e "${YELLOW}WARNING: $1${NC}"; }
print_error() { echo -e "${RED}ERROR: $1${NC}"; }
command_exists() { command -v "$1" &> /dev/null; }

# --- Core Functions ---

check_system() {
    print_info "Performing system checks..."

    if [ -f /etc/os-release ]; then . /etc/os-release; OS_ID=$ID; else print_error "Cannot detect Linux distribution."; exit 1; fi

    if ! command_exists python3; then print_error "Python 3 is not installed. Please install it and re-run."; exit 1; fi

    local py_ver; py_ver=$(python3 --version 2>&1 | awk '{print $2}')
    if awk -v min_ver="$MIN_PYTHON_VERSION" -v py_ver="$py_ver" 'BEGIN {if (py_ver < min_ver) exit 1}'; then
        print_error "Default Python version ($py_ver) is too old. YT-Archiver requires Python $MIN_PYTHON_VERSION or newer."
        
        # --- NEW LOGIC: Handle old Python on Debian/Ubuntu ---
        if [[ "$OS_ID" == "ubuntu" || "$OS_ID" == "debian" ]]; then
            print_warning "Your system's default Python is too old. We can try to install a newer version using the 'deadsnakes' PPA."
            if ! command_exists add-apt-repository; then
                print_info "'add-apt-repository' is needed. It is in the 'software-properties-common' package."
                read -p "May I install 'software-properties-common' with sudo? (y/n) " -n 1 -r; echo
                if [[ $REPLY =~ ^[Yy]$ ]]; then sudo apt update && sudo apt install -y software-properties-common; else print_error "Cannot proceed without 'add-apt-repository'."; exit 1; fi
            fi
            
            read -p "Do you want to add the PPA and install Python 3.10? (y/n) " -n 1 -r; echo
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                print_info "Adding ppa:deadsnakes/ppa..."
                sudo add-apt-repository ppa:deadsnakes/ppa -y
                print_info "Updating package list..."
                sudo apt update
                print_info "Installing python3.10 and its venv package..."
                sudo apt install -y python3.10 python3.10-venv
                if [ $? -eq 0 ]; then
                    PYTHON_CMD="python3.10" # CRITICAL: Update the command for subsequent steps
                    print_success "Successfully installed Python 3.10. The script will now use '${PYTHON_CMD}'."
                else
                    print_error "Failed to install Python 3.10. Aborting."; exit 1
                fi
            else
                print_error "User declined to install a newer Python version. Aborting."; exit 1
            fi
        else
            exit 1 # Exit if not on a Debian-based system with old Python
        fi
    else
        print_success "Python version $py_ver meets the requirement (>= $MIN_PYTHON_VERSION)."
    fi
}

install_dependencies() {
    print_info "Checking for other required dependencies..."
    local package_manager=""
    local packages_to_install=()

    case "$OS_ID" in
        ubuntu|debian|pop|mint) package_manager="apt" ;;
        fedora|centos|rhel) package_manager="dnf"; if ! command_exists dnf; then package_manager="yum"; fi ;;
        arch) package_manager="pacman" ;;
    esac

    local required_pkgs_map=(
        ["apt"]="git python3-pip ffmpeg"
        ["dnf"]="git python3-pip ffmpeg"
        ["yum"]="git python3-pip ffmpeg"
        ["pacman"]="git python-pip ffmpeg"
    )
    
    for pkg in ${required_pkgs_map[$package_manager]}; do
        local bin_name="$pkg"
        if [[ "$pkg" == "python3-pip" || "$pkg" == "python-pip" ]]; then bin_name="pip3"; fi
        if ! command_exists "$bin_name"; then packages_to_install+=("$pkg"); fi
    done

    if [ ${#packages_to_install[@]} -gt 0 ]; then
        print_warning "The following packages are missing: ${packages_to_install[*]}"
        read -p "May I install them using 'sudo $package_manager'? (y/n) " -n 1 -r; echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            if [[ "$package_manager" == "apt" ]]; then sudo apt update; fi
            sudo $package_manager install -y "${packages_to_install[@]}"
            if [ $? -ne 0 ]; then print_error "Package installation failed. Aborting."; exit 1; fi
        else
            print_error "User aborted dependency installation. Exiting."; exit 1
        fi
    fi

    if ! command_exists rclone; then
        print_warning "'rclone' is not installed."
        read -p "May I install it using the official script (curl | sudo bash)? (y/n) " -n 1 -r; echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            curl https://rclone.org/install.sh | sudo bash
            if ! command_exists rclone; then print_error "Rclone installation failed. Aborting."; exit 1; fi
        else
            print_error "User aborted rclone installation. Exiting."; exit 1
        fi
    fi
    print_success "All dependencies are satisfied."
}

setup_rclone() {
    print_info "Checking Rclone configuration..."
    if [ ! -f "$HOME/.config/rclone/rclone.conf" ] || [ ! -s "$HOME/.config/rclone/rclone.conf" ]; then
        print_warning "Rclone configuration not found or is empty."
        read -p "Do you want to run 'rclone config' now to set up your remote? (y/n) " -n 1 -r; echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then rclone config; else print_error "Rclone must be configured. Aborting."; exit 1; fi
    else
        print_success "Rclone configuration found. Available remotes:"; rclone listremotes
    fi
}

clone_repo() {
    print_info "Cloning the YT-Archiver repository..."
    if [ -d "$REPO_DIR" ]; then
        print_warning "Directory '$REPO_DIR' already exists."
        read -p "Do you want to [c]ontinue with the existing directory or [s]tart over (deletes existing)? (c/s) " -n 1 -r; echo
        if [[ $REPLY =~ ^[Ss]$ ]]; then rm -rf "$REPO_DIR"; git clone "$REPO_URL"; fi
    else
        git clone "$REPO_URL"
    fi
    cd "$REPO_DIR" || { print_error "Could not change to repository directory '$REPO_DIR'. Aborting."; exit 1; }
    print_success "Repository is ready in $(pwd)"
}

setup_python_env() {
    print_info "Setting up Python virtual environment using '${PYTHON_CMD}'..."
    # Use the determined Python command to create the venv
    ${PYTHON_CMD} -m venv venv
    source venv/bin/activate
    print_info "Installing Python packages from requirements.txt..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then print_error "Failed to install Python dependencies. Aborting."; deactivate; exit 1; fi
    deactivate
    print_success "Python environment is set up."
}

configure_app() {
    print_info "Configuring the application (config.ini)..."
    cp config.ini.template config.ini
    echo "Please provide the following configuration details:"
    read -p "Enter the YouTube Channel URL or ID: " channel_url
    read -p "Enter your Rclone remote name (e.g., 'mega'): " remote_name
    read -p "Enter the path on the remote storage (e.g., '/YouTube/MyChannel'): " remote_path
    read -p "Enter your YouTube Data API Key (optional, press Enter to skip): " api_key

    escaped_channel_url=$(printf '%s\n' "$channel_url" | sed 's:[][\\/.^$*]:\\&:g')
    escaped_remote_name=$(printf '%s\n' "$remote_name" | sed 's:[][\\/.^$*]:\\&:g')
    escaped_remote_path=$(printf '%s\n' "$remote_path" | sed 's:[][\\/.^$*]:\\&:g')
    escaped_api_key=$(printf '%s\n' "$api_key" | sed 's:[][\\/.^$*]:\\&:g')

    sed -i "s|^channel_url =.*|channel_url = ${escaped_channel_url}|" config.ini
    sed -i "s|^remote_name =.*|remote_name = ${escaped_remote_name}|" config.ini
    sed -i "s|^remote_path =.*|remote_path = ${escaped_remote_path}|" config.ini
    if [ -n "$api_key" ]; then sed -i "s|^youtube_api_key =.*|youtube_api_key = ${escaped_api_key}|" config.ini; else sed -i "s|^youtube_api_key =.*|youtube_api_key =|" config.ini; fi
    print_success "config.ini has been configured."
}

create_and_install_service() {
    print_info "Setup complete! What would you like to do next?"
    select choice in "Run the archiver once (in the foreground)" "Set up as a background service (systemd)" "Exit"; do
        case $choice in
            "Run the archiver once (in the foreground)")
                print_info "Starting the archiver. Press CTRL+C to stop it."
                source venv/bin/activate
                python3 main.py
                deactivate
                break
                ;;
            "Set up as a background service (systemd)")
                print_info "Generating systemd service file..."
                local work_dir; work_dir=$(pwd)
                # The venv ensures the correct python is used, regardless of how it was created
                local exec_start="${work_dir}/venv/bin/python ${work_dir}/main.py"
                local user; user=$(whoami)
                local group; group=$(id -gn "$user")

                cat << EOF > yt-archiver.service.tmp
[Unit]
Description=YT-Archiver - YouTube Channel Archival Service
After=network.target

[Service]
User=${user}
Group=${group}
WorkingDirectory=${work_dir}
ExecStart=${exec_start}
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
                print_success "Service file 'yt-archiver.service.tmp' generated successfully."

                print_info "The following commands will be run with sudo:"
                echo "  sudo cp yt-archiver.service.tmp /etc/systemd/system/yt-archiver.service"
                echo "  sudo systemctl daemon-reload"
                echo "  sudo systemctl enable yt-archiver.service"
                echo "  sudo systemctl start yt-archiver.service"
                read -p "Do you want to proceed? (y/n) " -n 1 -r; echo
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    sudo cp yt-archiver.service.tmp /etc/systemd/system/yt-archiver.service
                    sudo systemctl daemon-reload
                    sudo systemctl enable yt-archiver.service
                    sudo systemctl start yt-archiver.service
                    print_success "Service has been set up and started."
                    print_info "You can check its status with: sudo systemctl status yt-archiver.service"
                    print_info "Logs can be viewed with: journalctl -u yt-archiver.service -f"
                else
                    print_warning "Service setup aborted by user."
                fi
                rm yt-archiver.service.tmp
                break
                ;;
            "Exit")
                break
                ;;
        esac
    done
}

# --- Script Execution ---
main() {
    clear
    echo "======================================================"
    echo "  YT-Archiver Definitive Setup Script (v3.1)        "
    echo "======================================================"
    echo
    check_system
    install_dependencies
    setup_rclone
    clone_repo
    setup_python_env
    configure_app
    create_and_install_service
    echo
    print_success "All done! The YT-Archiver is located in the '$(pwd)' directory."
}

main

Vagrant.configure("2") do |config|
  config.vm.box = "ubuntu/jammy64"
  config.vm.network "forwarded_port", guest: 8501, host: 8501
  config.vm.synced_folder ".", "/vagrant"

  config.vm.provision "shell", inline: <<-SHELL
    apt-get update -y
    apt-get install -y python3 python3-venv python3-pip
    cd /vagrant
    python3 -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt
  SHELL
end

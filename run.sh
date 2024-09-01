sudo mkdir /var/log/tgmusicbot
sudo chmod 777 /var/log/tgmusicbot
docker build . -t tgmusicbot
docker-compose up -d --remove-orphans

# Hive HA 4.0.1 avec Ansible — lab 3 VM

## Ce que deploie ce paquet

- `master` (`192.168.56.10`) : Hive Metastore `9083` + HiveServer2 `10000`.
- `worker1` (`192.168.56.11`) : un deuxieme Hive Metastore `9083` + HiveServer2 `10000`.
- ZooKeeper existant `master:2181,worker1:2181,worker2:2181` pour la decouverte des HiveServer2.
- Connexion SQL vers MySQL sur master **pour l'instant** (pas de MySQL Router ni de MySQL HA tant que le cluster n'a qu'un membre).
- Hadoop/HDFS existant sous `/opt/hadoop`, Java 11, Hive 4.0.1 deja installe sur master sous `/home/vagrant/apache-hive-4.0.1-bin`.

**Ce paquet ne modifie pas la base `metastore_db` et n'execute PAS `schematool -initSchema`.** Il conserve les parametres existants du `hive-site.xml` source, y compris l'utilisateur et le mot de passe JDBC. Il n'installe pas MySQL et ne reconfigure pas le groupe de replication.

## Installer ce paquet depuis Windows

1. Telecharger le ZIP joint depuis la reponse ChatGPT.
2. Copier le ZIP dans le projet Windows partage par Vagrant (`C:\cluster-deploy\bigdata-cluster\` si votre VM utilise ce chemin). Le fichier sera visible sous `/vagrant/hive_ha_ansible.zip`.
3. Depuis le terminal `master` :

```bash
cd /home/vagrant/cluster-ansible
python3 -m zipfile -e /vagrant/hive_ha_ansible.zip /home/vagrant/cluster-ansible
find roles/hive_ha playbooks/hive-ha.yml -maxdepth 3 -type f
```

Si le partage Vagrant est situe ailleurs, adapter le chemin du ZIP.

## Prerequis rapides

```bash
# ZooKeeper est actif : deja confirme par le trio leader/followers
for n in master worker1 worker2; do ssh "$n" 'sudo /opt/zookeeper/bin/zkServer.sh --config /etc/zookeeper status' | tail -n 1; done

# Sur master : MySQL ecoute-t-il aussi sur localhost:3306 ?
sudo ss -lnt | grep ':3306'

# Le compte hive doit pouvoir se connecter au MySQL depuis le worker1
sudo mysql -e "SELECT user,host FROM mysql.user WHERE user='hive';"

# Hive source et Hadoop
ls -l /home/vagrant/apache-hive-4.0.1-bin/conf/hive-site.xml
ls -l /opt/hadoop/bin/hdfs
```

Si MySQL sur `master` n'ecoute pas sur `127.0.0.1:3306`, corriger d'abord sa configuration avec les deux adresses `127.0.0.1,192.168.56.10` et faire redemarrer MySQL proprement, en tenant compte du fait que le cluster MySQL est partiellement configure. L'autre solution est de modifier `hive_ha_db_endpoints.master` dans les defaults pour utiliser l'IP privee, mais il faut alors autoriser explicitement `hive@192.168.56.10` et verifier le mot de passe.

Si le port 2181 n'est pas joignable depuis la VM Hive vers sa propre IP `192.168.56.x`, corriger le binding de ZooKeeper. Le fait que `zkServer.sh status` imprime "Client address: localhost" ne prouve pas a lui seul un probleme : la verification de port de ce playbook tranchera.

## Executer Ansible

```bash
cd /home/vagrant/cluster-ansible
ansible-playbook -i inventory/hosts.ini playbooks/hive-ha.yml --syntax-check
ansible-playbook -i inventory/hosts.ini playbooks/hive-ha.yml
```

Si votre configuration Ansible demande sudo ou Vault, ajouter `-K` et/ou `--ask-vault-pass` suivant votre installation. Ce playbook n'utilise aucune variable Vault additionnelle : il lit les parametres JDBC actuels du Hive source.

L'archive temporaire `/home/vagrant/hive-4.0.1-ha-source.tar.gz` conserve une copie du Hive source, donc possiblement des informations sensibles. Elle est creee en mode `0600`, **hors du repo Git**. Pour redistribuer une version Hive mise a jour, supprimer cette archive puis relancer le playbook apres validation.

## Pourquoi ces fichiers ?

- `roles/hive_ha/defaults/main.yml` : versions, chemins, IP et ports. C'est le fichier a modifier pour passer plus tard a MySQL Router.
- `roles/hive_ha/tasks/main.yml` : copie Hive vers worker1, verifie ports/pre-requis, genere deux fichiers de config distincts, installe/demarre les services.
- `roles/hive_ha/files/configure_hive_ha.py` : met a jour XML sans avoir besoin d'une collection Ansible `community.general` et sans imprimer les secrets.
- `roles/hive_ha/templates/*.service.j2` : deux services `systemd` robustes, executes sous `vagrant` et redemarres en cas de panne du processus.
- `roles/hive_ha/handlers/main.yml` : redemarre les services quand la configuration change.
- `playbooks/hive-ha.yml` : orchestre l'archivage depuis master et le deploiement sur master puis worker1.

## Verification apres deploiement

```bash
ansible 'master,worker1' -i inventory/hosts.ini -b -m shell -a '
  hostname
  systemctl is-active hive-metastore
  systemctl is-active hive-hs2
  ss -lnt | grep -E ":9083|:10000"
'

# Depuis master, ouverture d'une NOUVELLE connexion Beeline par ZooKeeper
/home/vagrant/apache-hive-4.0.1-bin/bin/beeline \
  -u 'jdbc:hive2://master:2181,worker1:2181,worker2:2181/;serviceDiscoveryMode=zooKeeper;zooKeeperNamespace=hiveserver2' \
  -n vagrant -e 'SHOW DATABASES;'
```

Logs :

```bash
sudo journalctl -u hive-metastore -n 70 --no-pager
sudo journalctl -u hive-hs2 -n 70 --no-pager
sudo systemctl status hive-metastore hive-hs2 --no-pager
```

## Test de basculement de HiveServer2

1. Verifier les DEUX ports `10000` et une connexion Beeline positive.
2. Sur `master`, `sudo systemctl stop hive-hs2` (NE PAS arreter la VM `master`, car elle contient aussi MySQL et le NameNode HDFS uniques).
3. Depuis `master` ou une VM cliente, ouvrir **une nouvelle connexion Beeline via ZooKeeper**, executer `SHOW DATABASES;` et confirmer que `worker1` repond.
4. `sudo systemctl start hive-hs2` pour restaurer la redondance.

Les connexions deja etablies peuvent perdre leur session. La bascule d'une nouvelle connexion n'est pas la migration d'une session en cours.

## Limites et migration vers MySQL Router

Tant que InnoDB Cluster n'a qu'un membre, MySQL et le NameNode HDFS restent des points de defaillance uniques. La redondance des composants Hive ne signifie pas HA bout-en-bout.

Lorsque l'InnoDB Cluster compte 3 membres `ONLINE` et que **MySQL Router est reelement bootstrappe et teste sur les deux noeuds**, changer seulement :

```yaml
hive_ha_db_endpoints:
  master: "127.0.0.1:6446"
  worker1: "127.0.0.1:6446"
```

Puis relancer le playbook ; il regenerera les XML, puis redemarrera les services. Avant de le faire, verifier que l'utilisateur JDBC `hive` est autorise a se connecter au primaire via Router depuis les adresses des deux VM, avec le mot de passe deja present dans `hive-site.xml`.

## Depannage frequent

- **Port 3306 refuse sur master** : port `127.0.0.1:3306` absent ou MySQL inactif ; verifier le `bind-address` avant de toucher a Hive.
- **Metastore 9083 absent** : `journalctl -u hive-metastore`; regarder JDBC (acces, driver, mot de passe, schema).
- **HS2 10000 absent** : `journalctl -u hive-hs2`; verifier ZooKeeper 2181, Metastore 9083, Java 11, HDFS et memoire disponible.
- **User vagrant / permissions** : les XML generes contiennent un mot de passe, ils sont crees en `0600` au nom de `vagrant`.
- **Pas assez de RAM sur worker1** : les programmes Hadoop, ZooKeeper, MySQL, Hive Metastore et HiveServer2 consomment beaucoup ; augmenter les ressources VirtualBox si necessaire.
- **Configuration `hive-site.xml` atypique** : le script refuse de continuer si JDBC n'est pas `jdbc:mysql://.../metastore_db` ou si l'utilisateur / mot de passe manquent, afin d'eviter de creer une configuration invalide.

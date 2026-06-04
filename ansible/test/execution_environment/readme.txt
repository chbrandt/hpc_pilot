Building an EE: https://docs.ansible.com/projects/ansible/latest/getting_started_ee/build_execution_environment.html
ansible-builder build --tag postgresql_ee --container-runtime docker

Running the EE: https://docs.ansible.com/projects/ansible/latest/getting_started_ee/run_execution_environment.html
ansible-navigator run test_localhost.yml --execution-environment-image postgresql_ee --mode stdout --pull-policy missing --container-options=--user=0 --container-engine docker


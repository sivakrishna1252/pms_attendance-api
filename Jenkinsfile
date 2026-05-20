pipeline {
    agent any

    environment {
        PROJECT_DIR = "attendance_service"
        APP_NAME = "hrms-attendance-prod"
        IMAGE_NAME = "hrms-attendance-backend"
        ENV_ID = "hrms_attendance_env"
        ENV_FILE = ".env"
        ALLOWED_HOSTS_VALUE = "127.0.0.1,localhost,nexus-hrms.aspune.cloud"
        HOST_PORT = "6015"
        CONTAINER_PORT = "8000"
    }

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build Docker Image') {
            steps {
                withCredentials([file(credentialsId: "${ENV_ID}", variable: 'HRMS_ENV_FILE')]) {
                    sh """
                        cd ${PROJECT_DIR}
                        cp "\$HRMS_ENV_FILE" ${ENV_FILE}
                        echo "${ENV_FILE} refreshed from Jenkins credential ${ENV_ID}"
                        docker build -f Dockerfile.prod -t ${IMAGE_NAME}:${BUILD_NUMBER} -t ${IMAGE_NAME}:latest .
                    """
                }
            }
        }

        stage('Validate App') {
            steps {
                withCredentials([file(credentialsId: "${ENV_ID}", variable: 'HRMS_ENV_FILE')]) {
                    sh """
                        cd ${PROJECT_DIR}
                        cp "\$HRMS_ENV_FILE" ${ENV_FILE}
                        docker run --rm --env-file ${ENV_FILE} -e ALLOWED_HOSTS="${ALLOWED_HOSTS_VALUE}" ${IMAGE_NAME}:${BUILD_NUMBER} python manage.py check
                    """
                }
            }
        }

        stage('Deploy') {
            steps {
                withCredentials([file(credentialsId: "${ENV_ID}", variable: 'HRMS_ENV_FILE')]) {
                    sh """
                        cd ${PROJECT_DIR}
                        cp "\$HRMS_ENV_FILE" ${ENV_FILE}
                        docker rm -f ${APP_NAME} || true
                        docker run -d \\
                          --name ${APP_NAME} \\
                          --restart unless-stopped \\
                          --env-file ${ENV_FILE} \\
                          -e ALLOWED_HOSTS="${ALLOWED_HOSTS_VALUE}" \\
                          -p ${HOST_PORT}:${CONTAINER_PORT} \\
                          ${IMAGE_NAME}:${BUILD_NUMBER}
                    """
                }
            }
        }
    }

    post {
        success {
            echo "HRMS attendance API deployed on host port ${HOST_PORT} (local: http://127.0.0.1:${HOST_PORT}/, public: http://nexus-hrms.aspune.cloud/ or https per nginx)."
        }
        failure {
            echo "Pipeline failed. Check build logs."
        }
    }
}

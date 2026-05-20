pipeline {
    agent any

    environment {
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
                    sh '''
                        if [ -f Dockerfile.prod ]; then
                          APP_ROOT=.
                        elif [ -f attendance_service/Dockerfile.prod ]; then
                          APP_ROOT=attendance_service
                        else
                          echo "Dockerfile.prod not found at repo root or attendance_service/"
                          exit 1
                        fi
                        cd "$APP_ROOT"
                        cp "$HRMS_ENV_FILE" .env
                        echo ".env refreshed from Jenkins credential hrms_attendance_env (cwd=$(pwd))"
                        docker build -f Dockerfile.prod -t hrms-attendance-backend:${BUILD_NUMBER} -t hrms-attendance-backend:latest .
                    '''
                }
            }
        }

        stage('Validate App') {
            steps {
                withCredentials([file(credentialsId: "${ENV_ID}", variable: 'HRMS_ENV_FILE')]) {
                    sh '''
                        if [ -f Dockerfile.prod ]; then APP_ROOT=.; elif [ -f attendance_service/Dockerfile.prod ]; then APP_ROOT=attendance_service; else exit 1; fi
                        cd "$APP_ROOT"
                        cp "$HRMS_ENV_FILE" .env
                        docker run --rm --env-file .env -e ALLOWED_HOSTS="127.0.0.1,localhost,nexus-hrms.aspune.cloud" hrms-attendance-backend:${BUILD_NUMBER} python manage.py check
                    '''
                }
            }
        }

        stage('Deploy') {
            steps {
                withCredentials([file(credentialsId: "${ENV_ID}", variable: 'HRMS_ENV_FILE')]) {
                    sh '''
                        if [ -f Dockerfile.prod ]; then APP_ROOT=.; elif [ -f attendance_service/Dockerfile.prod ]; then APP_ROOT=attendance_service; else exit 1; fi
                        cd "$APP_ROOT"
                        cp "$HRMS_ENV_FILE" .env
                        docker rm -f hrms-attendance-prod || true
                        docker run -d \
                          --name hrms-attendance-prod \
                          --restart unless-stopped \
                          --env-file .env \
                          -e ALLOWED_HOSTS="127.0.0.1,localhost,nexus-hrms.aspune.cloud" \
                          -p 6015:8000 \
                          hrms-attendance-backend:${BUILD_NUMBER}
                    '''
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
